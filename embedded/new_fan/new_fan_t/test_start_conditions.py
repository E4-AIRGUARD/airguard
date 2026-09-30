"""Start gate tests: both sensor conditions must pass before recording begins."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import measure_thermal_response as thermal
from test_measure_thermal_response import FakeSerial, sample, EXPECTED_HEADER


class StartConditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_case(self, packets, before_read=None, poll=None, extra=(), check=None):
        fake = FakeSerial(packets)
        camera = Mock()
        camera.count = 0
        camera.begin.side_effect = lambda: setattr(camera, 'count', 1)
        if check:
            camera.check.side_effect = lambda: check(fake, camera)
        original_csvs = set(self.root.glob('*.csv'))
        def before(reads):
            if reads > 50:
                raise AssertionError('Unexpected unbounded wait in test')
            if before_read:
                before_read(fake, camera, reads)
        fake.before_read = before
        args = ['test_gate', '--duration-min', '0.05', '--intake', '35', '--exhaust', '60',
                '--out-dir', str(self.root), '--image-dir', str(self.root / 'images'), *extra]
        error = None
        returned = None
        with patch.object(thermal, 'CameraRecorder', return_value=camera) as factory, \
                patch.object(thermal.serial, 'Serial', return_value=fake), \
                patch.object(thermal.time, 'sleep'), \
                patch.object(thermal.time, 'monotonic', side_effect=lambda: fake.now), \
                patch.object(thermal.HeatKeyReader, 'poll', lambda keys: poll(keys, fake) if poll else None), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            camera.open.side_effect = lambda: factory.call_args.args[0].mkdir(parents=True)
            try:
                returned = thermal.main(args)
            except SystemExit as exc:
                error = str(exc)
        paths = set(self.root.glob('*.csv')) - original_csvs
        rows = []
        if paths:
            self.assertEqual(len(paths), 1)
            with paths.pop().open(encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, EXPECTED_HEADER)
                rows = list(reader)
        return fake, camera, rows, returned, error

    def test_inclusive_boundaries_and_same_sample_requirement(self):
        for temp in (22, 24, 26):
            for pm in (17, 19, 20):
                self.assertTrue(thermal.start_conditions_ok(thermal.parse_data_line(sample(temp=temp, pm25=pm).decode().strip())))
        for temp, pm in ((21.99, 19), (26.01, 19), (24, 16), (24, 21), (27, 18), (24, 25)):
            self.assertFalse(thermal.start_conditions_ok(thermal.parse_data_line(sample(temp=temp, pm25=pm).decode().strip())))
        self.assertFalse(thermal.start_conditions_ok(None))

    def test_bad_sensor_flags_and_nonfinite_values_never_pass(self):
        for kwargs in ({'stale':1}, {'err':1}, {'temp':'nan'}, {'temp':'inf'}, {'temp':'-inf'}, {'pm25':'nan'}):
            params = {'pm25':19, **kwargs}
            d = thermal.parse_data_line(sample(**params).decode().strip())
            self.assertFalse(thermal.start_conditions_ok(d))

    def test_wait_excluded_from_csv_camera_and_duration(self):
        before_gate = [sample(temp=24, pm25=21), sample(temp=27, pm25=19), sample(temp=21, pm25=19),
                       sample(pm25=16), sample(pm25=19, stale=1), sample(pm25=19, err=1),
                       sample(temp=22, pm25=17)]
        def inspect(fake, camera, reads):
            if reads < len(before_gate):
                self.assertEqual(list(self.root.glob('*.csv')), [])
                camera.begin.assert_not_called()
                self.assertEqual(fake.commands, [])
        fake, camera, rows, complete, error = self.run_case(
            [*before_gate, sample(temp=30, pm25=80), sample(temp=39, pm25=200), sample(temp=40, pm25=300)],
            before_read=inspect)
        self.assertIsNone(error)
        self.assertTrue(complete)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r['PM2.5'] for r in rows], ['80','200','300'])
        self.assertEqual([r['Temp'] for r in rows], ['30.0','39.0','40.0'])
        self.assertEqual(fake.now, len(before_gate) + 3)
        camera.begin.assert_called_once()
        camera.close.assert_called_once()
        self.assertEqual(fake.commands, [b'F 35 60\n', b'0\n'])
        check = json.loads(next(self.root.glob('*.start_check.json')).read_text(encoding='utf-8'))
        self.assertEqual((check['temp_c'],check['pm2_5']), (22,17))

    def test_malformed_and_empty_data_keep_waiting(self):
        fake, _, rows, complete, error = self.run_case(
            [b'', b'not DATA\n', b'DATA,broken\n', sample(pm25=19), sample(), sample(), sample()])
        self.assertIsNone(error)
        self.assertTrue(complete)
        self.assertEqual(len(rows), 3)
        self.assertEqual(fake.reads, 7)

    def test_q_cancels_before_recording_and_closes_devices(self):
        def poll(keys, fake):
            if fake.reads >= 2:
                keys.quit = True
        fake, camera, rows, complete, error = self.run_case([sample(pm25=99)] * 3, poll=poll)
        self.assertIsNone(error)
        self.assertFalse(complete)
        self.assertEqual(rows, [])
        self.assertEqual(list(self.root.glob('*.csv')), [])
        self.assertEqual(list(self.root.glob('*.start_check.json')), [])
        camera.begin.assert_not_called()
        camera.close.assert_called_once()
        self.assertEqual(fake.commands, [b'0\n'])
        self.assertTrue(fake.closed)

    def test_stop_file_cancels_even_when_new_sample_would_pass(self):
        stop = self.root / 'STOP'
        def before(fake, camera, reads):
            if reads == 1:
                stop.touch()
        fake, camera, rows, complete, error = self.run_case(
            [sample(pm25=50), sample(pm25=19)], before_read=before, extra=['--stop-file',str(stop)])
        self.assertIsNone(error)
        self.assertFalse(complete)
        self.assertEqual(rows, [])
        camera.begin.assert_not_called()
        self.assertTrue(fake.closed)

    def test_serial_loss_while_waiting_is_failure_without_csv(self):
        fake, camera, rows, _, error = self.run_case([sample(pm25=50), thermal.serial.SerialException('unplugged')])
        self.assertIn('unplugged', error)
        self.assertEqual(rows, [])
        camera.begin.assert_not_called()
        self.assertTrue(fake.closed)
        self.assertEqual(fake.commands, [b'0\n'])

    def test_camera_failure_while_waiting_is_reported(self):
        def check(fake, camera):
            if fake.reads >= 1:
                raise OSError('camera lost during preflight')
        fake, camera, rows, _, error = self.run_case([sample(pm25=99)], check=check)
        self.assertIn('camera lost during preflight', error)
        self.assertEqual(rows, [])
        camera.begin.assert_not_called()
        self.assertTrue(fake.closed)

    def test_thermal_cooling_flow_after_gate_is_unchanged(self):
        fake, camera, rows, complete, error = self.run_case(
            [sample(temp=24, pm25=19), sample(temp=37, pm25=19), sample(temp=38, pm25=25),
             sample(temp=37, pm25=50, intake=35, exhaust=60)], extra=['--heat-until-temp','38'])
        self.assertIsNone(error)
        self.assertTrue(complete)
        self.assertEqual(len(rows), 3)
        self.assertEqual(fake.commands, [b'0\n', b'F 35 60\n', b'0\n'])
        camera.begin.assert_called_once()

    def test_preflight_is_repeated_for_each_measurement(self):
        for _ in range(2):
            fake, _, rows, complete, error = self.run_case(
                [sample(pm25=50), sample(pm25=20, temp=26), sample(), sample(), sample()])
            self.assertIsNone(error)
            self.assertTrue(complete)
            self.assertEqual(fake.reads, 5)
            self.assertEqual(len(rows), 3)


if __name__ == '__main__':
    unittest.main()
