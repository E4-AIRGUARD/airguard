import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import measure_heat_cycle as cycle


class HeatCycleTests(unittest.TestCase):
    def test_status_retries_temporary_windows_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text('{"rows": 1}')
            real_replace = Path.replace
            attempts = []

            def locked_replace(source, destination):
                attempts.append(source)
                if len(attempts) <= 2:
                    raise PermissionError('temporary reader lock')
                return real_replace(source, destination)

            with patch.object(Path, 'replace', locked_replace), patch.object(cycle.time, 'sleep'):
                cycle.save_status(path, {'rows': 2})
            self.assertEqual(json.loads(path.read_text()), {'rows': 2})
            self.assertEqual(len(attempts), 3)

    def test_persistent_status_lock_is_bounded_and_preserves_last_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text('{"rows": 1}')
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')) as replace, \
                    patch.object(cycle.time, 'sleep'), self.assertRaises(PermissionError):
                cycle.save_status(path, {'rows': 2})
            self.assertEqual(replace.call_count, 11)
            self.assertEqual(json.loads(path.read_text()), {'rows': 1})

    def run_case(self, *, target_at=45, recover=True, fault=None, combination=1,
                 initial_temp=24, pm=0, stale=0, stop_chatter=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = []
            captures = []
            notifications = []

            class Serial:
                now = 0
                started = None
                is_open = False
                pair = (0, 0)
                emitted_stop_chatter = False

                def open(self):
                    self.is_open = True

                def close(self):
                    self.is_open = False

                def reset_input_buffer(self):
                    pass

                def write(self, value):
                    commands.append((self.now, value))
                    if value.startswith(b'F '):
                        self.pair = tuple(map(int, value.split()[1:]))
                        self.started = self.now
                    else:
                        self.pair = (0, 0)

                def readline(self):
                    if (stop_chatter and self.started is not None and self.pair == (0, 0)
                            and not self.emitted_stop_chatter):
                        self.emitted_stop_chatter = True
                        self.now += 0.01
                        return b'[FAN] stopped\n'
                    self.emitted_stop_chatter = False
                    self.now += 1
                    elapsed = self.now - self.started if self.started is not None else 0
                    if ((fault == 'serial' and elapsed >= 50)
                            or (fault == 'serial_at_cap' and elapsed >= 58)
                            or (fault == 'cool_serial' and elapsed >= 305)):
                        return b''
                    if fault == 'stop' and elapsed == 50:
                        (root / 'STOP').touch()
                    i, o = self.pair
                    temp = initial_temp if self.now < 4 else 24
                    if self.started is not None:
                        temp = 24 if elapsed < 30 else 35
                        if target_at is not None and elapsed >= target_at:
                            temp = 40 if elapsed < target_at + 2 else (24 if recover else 30)
                        if elapsed >= 300:
                            temp = 30 if elapsed < 310 else 24
                    err = 1 if fault == 'bme' and elapsed >= 40 else 0
                    if fault == 'cool_bme' and elapsed >= 305:
                        err = 1
                    humidity = 'nan' if fault == 'humidity' and elapsed >= 40 else '60'
                    ri = 0 if fault == 'fan' else (1000 if i else 0)
                    if fault == 'cool_fan' and elapsed >= 305:
                        ri = 0
                    return (f'DATA,{self.now * 1000},{i},{ri},{o},{2000 if o else 0},'
                            f'{temp},{humidity},1000,0,{pm},0,{err},{stale}\n').encode()

            port = Serial()
            camera = Mock(count=0)
            camera.begin.side_effect = lambda: captures.append(('begin', port.now))

            def pause():
                captures.append(('pause', port.now))
                if len(captures) == 2 and captures[0][0] == 'begin':
                    camera.count = port.now - captures[0][1]

            camera.pause.side_effect = pause
            if fault == 'camera':
                def camera_check():
                    if port.started is not None and port.now - port.started >= 50:
                        raise OSError('camera failed')
                camera.check.side_effect = camera_check
            with patch.object(cycle.serial, 'Serial', return_value=port), \
                    patch.object(cycle, 'CameraRecorder', return_value=camera), \
                    patch.object(cycle.time, 'monotonic', side_effect=lambda: port.now), \
                    patch.object(cycle, 'notify', side_effect=lambda message:
                                 notifications.append((port.now - (port.started or 0), message))), \
                    contextlib.redirect_stdout(io.StringIO()):
                argv = ['--session-dir', str(root), '--combination', str(combination)]
                if fault:
                    with self.assertRaises(SystemExit):
                        cycle.main(argv)
                else:
                    cycle.main(argv)
            run = next(root.glob('*_intake_*'))
            status = json.loads((run / 'status.json').read_text(encoding='utf-8'))
            status['_test_notifications'] = notifications
            events = [json.loads(s) for s in (run / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            with next(run.glob('*.csv')).open(encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, cycle.CSV_HEADER)
                rows = list(reader)
            self.assertFalse(port.is_open)
            camera.close.assert_called_once()
            self.assertEqual(commands[-1][1], b'0\n')
            cooling_dir = root / 'cooling' / run.name
            if (cooling_dir / 'status.json').exists():
                status['_test_cooling'] = json.loads((cooling_dir / 'status.json').read_text())
                with (cooling_dir / 'cooling.csv').open() as f:
                    reader = csv.DictReader(f)
                    self.assertEqual(reader.fieldnames, cycle.CSV_HEADER)
                    status['_test_cooling_rows'] = list(reader)
            self.assertEqual(len(list((root / 'cooling').iterdir())), 9)
            return status, rows, events, commands, captures

    def test_full_cycle_uses_one_300_second_timer_and_constant_pair(self):
        status, rows, events, commands, captures = self.run_case(pm=321)
        self.assertEqual(status['status'], 'complete')
        self.assertEqual(status['outcome'], 'returned_to_start_range')
        self.assertEqual(status['elapsed_sec'], 300)
        self.assertTrue(status['stop_verified'])
        self.assertEqual([v for _, v in commands if v.startswith(b'F ')], [b'F 35 35\n'])
        cues = {e['event']: e['elapsed_sec'] for e in events}
        self.assertEqual(cues['heat_on_instruction'], 30)
        self.assertEqual(cues['heat_off_instruction'], 60)
        self.assertEqual(status['heating_duration_sec'], 30)
        self.assertEqual(status['protocol'], 'fixed_30s_heat_then_same_duty_cooling')
        self.assertEqual([(t, msg) for t, msg in status['_test_notifications']
                          if msg.startswith('30초 가열 종료:')],
                         [(60, '30초 가열 종료: 지금 드라이기를 끄세요. 팬은 같은 속도로 유지하며 회복을 측정합니다.')])
        self.assertEqual(cues['measurement_ended'], 300)
        self.assertEqual(captures[1][1] - captures[0][1], 300)
        self.assertTrue(all(r['PM2.5'] == '321' and float(r['Humidity']) == 60 for r in rows))
        self.assertTrue(all(r['Intake_PWM'] == '35' and r['Exhaust_PWM'] == '35' for r in rows))
        self.assertTrue(all(r['Heat_Load'] == '0' for r in rows[:29]))
        self.assertTrue(all(r['Heat_Load'] == '1' for r in rows[29:59]))
        self.assertTrue(all(r['Heat_Load'] == '0' for r in rows[59:]))
        self.assertEqual(status['images'], 300)
        self.assertTrue(status['next_start_ready'])
        cool = status['_test_cooling']
        self.assertEqual(cool['threshold_elapsed_sec'], 12)
        self.assertTrue(cool['stop_verified'])
        self.assertTrue(all(r['Heat_Load'] == '0' and r['Intake_PWM'] == '35' and r['Exhaust_PWM'] == '35'
                            for r in status['_test_cooling_rows']))
        # No stop or speed change between the 300 s measurement and cooling threshold.
        begin = next(t for t, v in commands if v.startswith(b'F '))
        self.assertFalse(any(v == b'0\n' and begin < t < begin + 312 for t, v in commands))

    def test_stop_verification_ignores_interleaved_firmware_text(self):
        status, _, _, _, _ = self.run_case(stop_chatter=True)
        self.assertEqual(status['status'], 'complete')
        self.assertTrue(status['stop_verified'])
        self.assertTrue(status['_test_cooling']['next_start_ready'])

    def test_start_waits_for_live_temperature_but_not_pm(self):
        status, _, _, commands, captures = self.run_case(initial_temp=28, pm=0)
        self.assertEqual(status['preheat_sample']['temp_c'], 24)
        self.assertEqual(captures[0], ('begin', 4))
        self.assertEqual(next(t for t, v in commands if v.startswith(b'F ')), 4)

    def test_target_missing_does_not_extend_300_seconds_or_claim_recovery(self):
        status, rows, events, _, _ = self.run_case(target_at=None)
        self.assertEqual(status['elapsed_sec'], 300)
        self.assertFalse(status['target_reached'])
        self.assertEqual(status['outcome'], 'target_not_reached')
        self.assertGreater(status['cooling_rows'], 200)
        self.assertEqual(rows[-1]['Heat_Load'], '0')
        off = [e for e in events if e['event'] == 'heat_off_instruction']
        self.assertEqual(len(off), 1)
        self.assertEqual(off[0]['reason'], 'fixed_duration_elapsed')
        self.assertEqual(off[0]['elapsed_sec'], 60)
        self.assertEqual(status['heating_instruction_duration_sec'], 30)

    def test_recovery_incomplete_and_late_target_are_distinguished(self):
        self.assertEqual(self.run_case(recover=False)[0]['outcome'], 'recovery_not_finished')
        self.assertEqual(self.run_case(target_at=299)[0]['outcome'], 'recovery_not_finished')

    def test_cap_works_without_new_sensor_data(self):
        status, _, events, _, _ = self.run_case(target_at=None, fault='serial_at_cap')
        off = next(e for e in events if e['event'] == 'heat_off_instruction')
        self.assertEqual(off['reason'], 'fixed_duration_elapsed')
        self.assertEqual(off['elapsed_sec'], 60)
        self.assertEqual(status['status'], 'failed')

    def test_40_degree_is_recorded_silently_and_heat_end_alarm_still_fires(self):
        status, _, events, _, _ = self.run_case(target_at=32)
        self.assertTrue(status['target_reached'])
        off = [e for e in events if e['event'] == 'heat_off_instruction']
        self.assertEqual(len(off), 1)
        self.assertEqual(off[0]['elapsed_sec'], 60)
        self.assertEqual(off[0]['reason'], 'fixed_duration_elapsed')
        crossed = [e for e in events if e['event'] == 'temperature_40_reached']
        self.assertEqual(len(crossed), 1)
        self.assertEqual(crossed[0]['elapsed_sec'], 32)
        self.assertFalse(any('40℃' in msg for _, msg in status['_test_notifications']))
        self.assertEqual([t for t, msg in status['_test_notifications']
                          if msg.startswith('30초 가열 종료:')], [60])

    def test_40_degree_reached_after_heating_is_recorded(self):
        status, _, events, _, _ = self.run_case(target_at=90)
        self.assertTrue(status['target_reached'])
        self.assertFalse(status['target_reached_during_heating'])
        crossed = next(e for e in events if e['event'] == 'temperature_40_reached')
        self.assertEqual(crossed['elapsed_sec'], 90)
        self.assertEqual(crossed['stage'], 'recovery')
        self.assertFalse(any('40℃' in msg for _, msg in status['_test_notifications']))

    def test_cooling_faults_preserve_main_measurement_and_stop(self):
        for fault in ('cool_serial', 'cool_bme', 'cool_fan'):
            with self.subTest(fault=fault):
                status, rows, _, _, _ = self.run_case(fault=fault)
                self.assertTrue(status['measurement_complete'])
                self.assertEqual(len(rows), 299)
                self.assertEqual(status['status'], 'failed')
                self.assertEqual(status['_test_cooling']['status'], 'failed')
                self.assertTrue(status['_test_cooling_rows'])

    def test_all_nine_pairs_and_stale_pm_keeps_column(self):
        for n, (_, intake, exhaust) in enumerate(cycle.COMBINATIONS, 1):
            with self.subTest(combination=n):
                status, rows, _, commands, _ = self.run_case(combination=n, stale=1)
                self.assertEqual(status['status'], 'complete')
                self.assertEqual([v for _, v in commands if v.startswith(b'F ')],
                                 [f'F {intake} {exhaust}\n'.encode()])
                self.assertTrue(all(r['PM2.5'] == '' for r in rows))

    def test_faults_preserve_partial_data_and_stop(self):
        for fault in ('serial', 'bme', 'humidity', 'fan', 'camera', 'stop'):
            with self.subTest(fault=fault):
                status, rows, _, _, _ = self.run_case(fault=fault)
                self.assertEqual(status['status'], 'stopped' if fault == 'stop' else 'failed')
                self.assertTrue(rows)
                self.assertLess(status['elapsed_sec'], 300)
                self.assertTrue(status['stop_verified'] or fault == 'serial')


if __name__ == '__main__':
    unittest.main()
