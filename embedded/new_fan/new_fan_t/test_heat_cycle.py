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
    def run_case(self, *, target_at=45, recover=True, fault=None, combination=1,
                 initial_temp=24, pm=0, stale=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = []
            captures = []

            class Serial:
                now = 0
                started = None
                is_open = False
                pair = (0, 0)

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
                    self.now += 1
                    elapsed = self.now - self.started if self.started is not None else 0
                    if fault == 'serial' and elapsed >= 50:
                        return b''
                    if fault == 'stop' and elapsed == 50:
                        (root / 'STOP').touch()
                    i, o = self.pair
                    temp = initial_temp if self.now < 4 else 24
                    if self.started is not None:
                        temp = 24 if elapsed < 30 else 35
                        if target_at is not None and elapsed >= target_at:
                            temp = 40 if elapsed < target_at + 2 else (24 if recover else 30)
                    err = 1 if fault == 'bme' and elapsed >= 40 else 0
                    humidity = 'nan' if fault == 'humidity' and elapsed >= 40 else '60'
                    ri = 0 if fault == 'fan' else (1000 if i else 0)
                    return (f'DATA,{self.now * 1000},{i},{ri},{o},{2000 if o else 0},'
                            f'{temp},{humidity},1000,0,{pm},0,{err},{stale}\n').encode()

            port = Serial()
            camera = Mock(count=0)
            camera.begin.side_effect = lambda: captures.append(('begin', port.now))

            def pause():
                captures.append(('pause', port.now))
                if captures[0][0] == 'begin':
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
                    patch.object(cycle, 'notify'), contextlib.redirect_stdout(io.StringIO()):
                argv = ['--session-dir', str(root), '--combination', str(combination)]
                if fault:
                    with self.assertRaises(SystemExit):
                        cycle.main(argv)
                else:
                    cycle.main(argv)
            run = next(root.glob('*_intake_*'))
            status = json.loads((run / 'status.json').read_text(encoding='utf-8'))
            events = [json.loads(s) for s in (run / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            with next(run.glob('*.csv')).open(encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, cycle.CSV_HEADER)
                rows = list(reader)
            self.assertFalse(port.is_open)
            camera.close.assert_called_once()
            self.assertEqual(commands[-1][1], b'0\n')
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
        self.assertEqual(cues['heat_off_instruction'], 45)
        self.assertEqual(cues['measurement_ended'], 300)
        self.assertEqual(captures[1][1] - captures[0][1], 300)
        self.assertTrue(all(r['PM2.5'] == '321' and float(r['Humidity']) == 60 for r in rows))
        self.assertTrue(all(r['Intake_PWM'] == '35' and r['Exhaust_PWM'] == '35' for r in rows))
        self.assertTrue(all(r['Heat_Load'] == '0' for r in rows[:29]))
        self.assertTrue(all(r['Heat_Load'] == '1' for r in rows[29:44]))
        self.assertTrue(all(r['Heat_Load'] == '0' for r in rows[44:]))

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
        self.assertEqual(status['cooling_rows'], 0)
        self.assertEqual(rows[-1]['Heat_Load'], '1')
        self.assertIn('measurement_deadline', [e.get('reason') for e in events])

    def test_recovery_incomplete_and_late_target_are_distinguished(self):
        self.assertEqual(self.run_case(recover=False)[0]['outcome'], 'recovery_not_finished')
        self.assertEqual(self.run_case(target_at=299)[0]['outcome'], 'no_recovery_samples')

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
