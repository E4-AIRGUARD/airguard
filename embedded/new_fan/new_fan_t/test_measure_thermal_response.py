"""Thermal logger regression tests; never open a real serial port."""
import contextlib
import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import measure_thermal_response as thermal


EXPECTED_HEADER = ['Time', 'Temp', 'Humidity', 'Exhaust_PWM', 'Intake_PWM',
                   'Heat_Load', 'Exhaust_RPM', 'Intake_RPM', 'PM2.5']


def sample(temp=25, rh=50, err=0, intake=0, exhaust=0, pm25=8, stale=0):
    return (f'DATA,1000,{intake},1200,{exhaust},2400,{temp},{rh},1013,1,{pm25},10,{err},{stale}\n').encode()


class FakeSerial:
    def __init__(self, packets=(), fail_write_at=None, reset_error=False, close_error=False):
        self.packets = iter(packets)
        self.commands = []
        self.now = 0
        self.reads = 0
        self.closed = False
        self.fail_write_at = fail_write_at
        self.reset_error = reset_error
        self.close_error = close_error
        self.before_read = None

    def reset_input_buffer(self):
        if self.reset_error:
            raise thermal.serial.SerialException('reset failed')

    def write(self, command):
        self.commands.append(command)
        if len(self.commands) == self.fail_write_at:
            raise thermal.serial.SerialException('write failed')
        return len(command)

    def readline(self):
        if self.before_read:
            self.before_read(self.reads)
        self.reads += 1
        self.now += 1
        packet = next(self.packets, b'')
        if isinstance(packet, BaseException):
            raise packet
        return packet

    def close(self):
        self.closed = True
        if self.close_error:
            raise thermal.serial.SerialException('close failed')


class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def argv(self, *extra):
        return ['phase_test', '--duration-min', '0.1', '--intake', '35', '--exhaust', '60',
                '--out-dir', str(self.folder), '--no-camera', *extra]

    def run_logger(self, fake, *extra, key_poll=None):
        error = None
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                patch.object(thermal.serial, 'Serial', return_value=fake) as serial_open, \
                patch.object(thermal.time, 'sleep'), \
                patch.object(thermal, 'wait_for_start_conditions', return_value={'temp_c':25,'pm2_5':19}), \
                patch.object(thermal.time, 'monotonic', side_effect=lambda: fake.now), \
                patch.object(thermal.HeatKeyReader, 'poll', key_poll or (lambda self: None)):
            try:
                thermal.main(self.argv(*extra))
            except SystemExit as exc:
                error = str(exc)
        paths = list(self.folder.glob('*.csv'))
        self.assertLessEqual(len(paths), 1)
        if not paths:
            return [], error, output.getvalue(), serial_open
        with paths[0].open(encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], EXPECTED_HEADER)
        self.assertTrue(all(len(row) == 9 for row in rows))
        return rows[1:], error, output.getvalue(), serial_open

    def test_only_ssot_fan_pairs_allowed(self):
        for intake in range(-1, 102):
            for exhaust in range(-1, 102):
                self.assertEqual(thermal.negative_pressure_ok(intake, exhaust),
                                 (intake, exhaust) in {(0, 0), (35, 60), (60, 100)})

    def test_invalid_arguments_rejected_before_serial_or_file_creation(self):
        cases = [('--duration-min', '0'), ('--duration-min', '-1'), ('--duration-min', 'nan'),
                 ('--duration-min', 'inf'), ('--intake', '0'), ('--intake', '20'),
                 ('--exhaust', '101'), ('--heat-until-temp', 'nan'),
                 ('--heat-until-temp', '38', '--heat-from-start'),
                 ('--intake', '0', '--exhaust', '0', '--heat-from-start'),
                 ('--event-at-min', 'nan:note'), ('--event-at-min', '-1:note'),
                 ('--event-at-min', '1:'), ('--event-at-min', 'oops'),
                 ('--camera-index', '-1'), ('--image-interval-sec', '0'),
                 ('--image-interval-sec', 'nan'), ('--image-interval-sec', 'inf')]
        with patch.object(thermal.serial, 'Serial') as serial_open, contextlib.redirect_stderr(io.StringIO()):
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    thermal.main(self.argv(*args))
            serial_open.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_help_works_with_windows_legacy_pipe_encoding(self):
        result = subprocess.run(
            [sys.executable, str(Path(thermal.__file__)), '--help'],
            env={**os.environ, 'PYTHONIOENCODING': 'cp949', 'PYTHONUTF8': '0'},
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertIn('38', result.stdout.decode('utf-8'))

    def test_parser_and_event(self):
        parsed = thermal.parse_data_line(sample().decode().strip())
        self.assertEqual(parsed['pm2_5'], 8)
        self.assertEqual(parsed['pms_stale'], 0)
        for line in ['[FAN] status', 'DATA,1,2', sample().decode().replace(',1200,', ',bad,')]:
            self.assertIsNone(thermal.parse_data_line(line))
        self.assertEqual(thermal.parse_event('0:note:detail'), (0.0, 'note:detail'))

    def test_baseline_schema_and_reported_values(self):
        fake = FakeSerial([sample()])
        rows, error, _, serial_open = self.run_logger(fake, '--intake', '0', '--exhaust', '0', '--port', 'TEST')
        self.assertIsNone(error)
        self.assertEqual(rows[0][1:], ['25.0', '50.0', '0', '0', '0', '2400', '1200', '8'])
        self.assertEqual(fake.commands, [b'F 0 0\n', b'0\n'])
        serial_open.assert_called_once_with('TEST', 115200, timeout=1, write_timeout=1)
        self.assertTrue(fake.closed)

    def test_only_valid_38_degree_sample_starts_cooling_once(self):
        fake = FakeSerial([sample(39, err=1), sample('inf'), sample('nan'), sample(37.9),
                           sample(38), sample(39, intake=35, exhaust=60)])
        rows, error, _, _ = self.run_logger(fake, '--heat-until-temp', '38')
        self.assertIsNone(error)
        self.assertEqual(fake.commands, [b'0\n', b'F 35 60\n', b'0\n'])
        self.assertEqual([r[1] for r in rows[:3]], ['', '', ''])
        self.assertEqual(rows[0][2], '')
        self.assertEqual(rows[4][3:6], ['0', '0', '0'])
        self.assertEqual(rows[5][3:5], ['60', '35'])

    def test_sustained_heat_and_manual_off(self):
        fake = FakeSerial([sample(30), sample(31)])
        def poll(keys):
            if fake.reads >= 1:
                keys.heat_load = 0
        rows, error, _, _ = self.run_logger(fake, '--heat-from-start', '--intake', '60',
                                           '--exhaust', '100', key_poll=poll)
        self.assertIsNone(error)
        self.assertEqual([r[5] for r in rows], ['1', '0'])
        self.assertEqual(fake.commands, [b'F 60 100\n', b'0\n'])

    def test_csv_flushed_before_next_read_and_preserved_on_disconnect(self):
        fake = FakeSerial([sample(), thermal.serial.SerialException('disconnected')], fail_write_at=2)
        def check_flushed(reads):
            if reads == 1:
                path = next(self.folder.glob('*.csv'))
                with path.open(encoding='utf-8', newline='') as f:
                    self.assertEqual(len(list(csv.reader(f))), 2)
        fake.before_read = check_flushed
        rows, error, output, _ = self.run_logger(fake)
        self.assertEqual(len(rows), 1)
        self.assertIn('disconnected', error)
        self.assertIn('정지 명령 전송 실패', output)
        self.assertTrue(fake.closed)

    def test_reset_failure_still_stops_and_closes(self):
        fake = FakeSerial(reset_error=True)
        rows, error, _, _ = self.run_logger(fake)
        self.assertEqual(rows, [])
        self.assertIn('reset failed', error)
        self.assertEqual(fake.commands, [b'0\n'])
        self.assertTrue(fake.closed)

    def test_initial_write_failure_still_stops_and_closes(self):
        fake = FakeSerial(fail_write_at=1)
        _, error, _, _ = self.run_logger(fake)
        self.assertIn('write failed', error)
        self.assertEqual(fake.commands, [b'F 35 60\n', b'0\n'])
        self.assertTrue(fake.closed)

    def test_threshold_sample_survives_cooling_write_failure(self):
        fake = FakeSerial([sample(38)], fail_write_at=2)
        rows, error, _, _ = self.run_logger(fake, '--heat-until-temp', '38')
        self.assertEqual(len(rows), 1)
        self.assertIn('write failed', error)
        self.assertTrue(fake.closed)

    def test_ctrl_c_preserves_samples(self):
        fake = FakeSerial([sample(), KeyboardInterrupt()])
        rows, error, _, _ = self.run_logger(fake)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(fake.commands[-1], b'0\n')
        self.assertTrue(fake.closed)

    def test_q_does_not_read_an_extra_sample(self):
        fake = FakeSerial([sample(), sample(38)])
        def poll(keys):
            keys.quit = fake.reads >= 1
        rows, error, _, _ = self.run_logger(fake, key_poll=poll)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(fake.reads, 1)
        self.assertTrue(fake.closed)

    def test_no_data_is_not_success(self):
        fake = FakeSerial([b'{"k":"sens"}\n'])
        rows, error, _, _ = self.run_logger(fake)
        self.assertEqual(rows, [])
        self.assertIn('DATA 샘플 없음', error)
        self.assertTrue(fake.closed)

    def test_unreached_temperature_is_not_success(self):
        fake = FakeSerial([sample(37.9)])
        rows, error, _, _ = self.run_logger(fake, '--heat-until-temp', '38')
        self.assertEqual(len(rows), 1)
        self.assertIn('목표온도 미도달', error)
        self.assertEqual(fake.commands, [b'0\n', b'0\n'])

    def test_close_failure_reported_without_losing_csv(self):
        rows, error, _, _ = self.run_logger(FakeSerial([sample()], close_error=True))
        self.assertEqual(len(rows), 1)
        self.assertIn('close failed', error)

    def test_camera_failure_preserves_sensor_data_and_stops_fans(self):
        fake = FakeSerial([sample(), sample(38)])
        with patch.object(thermal, 'CameraRecorder') as factory, \
                contextlib.redirect_stdout(io.StringIO()):
            camera = factory.return_value
            camera.count = 1
            camera.open.side_effect = lambda: factory.call_args.args[0].mkdir(parents=True)
            camera.check.side_effect = [None, OSError('camera disconnected')]
            args = [a for a in self.argv('--image-dir', str(self.folder / 'images')) if a != '--no-camera']
            with patch.object(thermal.serial, 'Serial', return_value=fake), \
                    patch.object(thermal, 'wait_for_start_conditions', return_value={'temp_c':25,'pm2_5':19}), \
                    patch.object(thermal.time, 'sleep'), \
                    patch.object(thermal.time, 'monotonic', side_effect=lambda: fake.now), \
                    patch.object(thermal.HeatKeyReader, 'poll', lambda self: None), \
                    self.assertRaisesRegex(SystemExit, 'camera disconnected'):
                thermal.main(args)
            camera.begin.assert_called_once()
            camera.close.assert_called_once()
        self.assertEqual(fake.commands[-1], b'0\n')
        self.assertTrue(fake.closed)
        with next(self.folder.glob('*.csv')).open(encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], EXPECTED_HEADER)
        self.assertEqual(len(rows), 2)
        manifest = next((self.folder / 'images').rglob('run.json'))
        self.assertTrue(manifest.exists())

    def test_camera_open_failure_never_opens_serial(self):
        with patch.object(thermal, 'CameraRecorder') as factory, \
                patch.object(thermal.serial, 'Serial') as serial_open, \
                contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.open.side_effect = OSError('camera unavailable')
            factory.return_value.count = 0
            args = [a for a in self.argv() if a != '--no-camera']
            with self.assertRaisesRegex(SystemExit, 'camera unavailable'):
                thermal.main(args)
            serial_open.assert_not_called()
            factory.return_value.close.assert_called_once()

    def test_clean_air_allows_nine_pairs_but_thermal_stays_restricted(self):
        for intake in (35, 60, 100):
            for exhaust in (35, 60, 100):
                args = thermal.parse_args(self.argv('--experiment', 'clean-air',
                                                    '--intake', str(intake), '--exhaust', str(exhaust)))
                self.assertEqual((args.intake, args.exhaust), (intake, exhaust))
        with contextlib.redirect_stderr(io.StringIO()):
            for extra in [('--heat-until-temp', '38'), ('--heat-from-start',), ('--intake', '0')]:
                with self.assertRaises(SystemExit):
                    thermal.parse_args(self.argv('--experiment', 'clean-air', *extra))
            with self.assertRaises(SystemExit):
                thermal.parse_args(self.argv('--intake', '35', '--exhaust', '35'))

    def test_clean_air_heat_key_is_disabled(self):
        keyboard = Mock()
        keyboard.kbhit.side_effect = [True, False]
        keyboard.getch.return_value = b'h'
        keys = thermal.HeatKeyReader(allow_heat=False)
        with patch.object(thermal, 'msvcrt', keyboard):
            keys.poll()
        self.assertEqual(keys.heat_load, 0)

    def test_clean_air_runs_equal_fans_with_heat_zero(self):
        fake = FakeSerial([sample(intake=35, exhaust=35)])
        rows, error, _, _ = self.run_logger(fake, '--experiment', 'clean-air', '--exhaust', '35')
        self.assertIsNone(error)
        self.assertEqual(rows[0][3:6], ['35', '35', '0'])
        self.assertEqual(fake.commands, [b'F 35 35\n', b'0\n'])

    def test_events_fire_once(self):
        _, error, output, _ = self.run_logger(FakeSerial([sample()]), '--event-at-min', '0:marker')
        self.assertIsNone(error)
        self.assertEqual(output.count('>>> 이벤트: marker'), 1)


if __name__ == '__main__':
    unittest.main()
