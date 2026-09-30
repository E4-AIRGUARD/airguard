import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import measure_heat_after_manual as manual


class ManualCoolingTests(unittest.TestCase):
    def run_case(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / 'baseline.json'
            baseline.write_text(json.dumps({'last': {'temp_c': 24, 'bme_err': 0}}))
            trace = []

            class Serial:
                now = 0
                is_open = False
                pair = (0, 0)
                def open(self):
                    self.is_open = True
                def close(self):
                    self.is_open = False
                def reset_input_buffer(self):
                    pass
                def write(self, value):
                    trace.append(value)
                    self.pair = tuple(map(int, value.split()[1:])) if value.startswith(b'F ') else (0, 0)
                def readline(self):
                    self.now += 1
                    if fail and self.now > 10:
                        return b''
                    i, o = self.pair
                    return f'DATA,{self.now * 1000},{i},{1000 if i else 0},{o},{2000 if o else 0},30,60,1000,0,0,0,0,0\n'.encode()

            port = Serial()
            camera = Mock(count=300)
            camera.begin.side_effect = lambda: trace.append('begin_camera')
            camera.pause.side_effect = lambda: trace.append('pause_camera')
            args = ['manual', '--session-dir', str(root / 'session'), '--combination', '3', '--preheat-check', str(baseline)]
            with patch.object(sys, 'argv', args), patch.object(manual.serial, 'Serial', return_value=port), \
                    patch.object(manual, 'CameraRecorder', return_value=camera), \
                    patch.object(manual.time, 'monotonic', side_effect=lambda: port.now), \
                    contextlib.redirect_stdout(io.StringIO()):
                if fail:
                    with self.assertRaises(SystemExit):
                        manual.main()
                else:
                    manual.main()
            run = root / 'session' / '03_intake_weak_exhaust_strong'
            status = json.loads((run / 'status.json').read_text(encoding='utf-8'))
            with next(run.glob('*.csv')).open(encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, manual.CSV_HEADER)
                rows = list(reader)
            self.assertFalse(port.is_open)
            camera.close.assert_called_once()
            self.assertEqual(trace[0:2], [b'F 35 100\n', 'begin_camera'])
            self.assertIn(b'0\n', trace)
            return status, rows

    def test_hot_post_heating_sample_starts_cooling_without_reapplying_baseline_gate(self):
        status, rows = self.run_case()
        self.assertEqual(status['status'], 'complete')
        self.assertEqual(status['elapsed_sec'], 300)
        self.assertTrue(status['stop_verified'])
        self.assertTrue(rows)
        self.assertTrue(all(r['Heat_Load'] == '0' and r['Intake_PWM'] == '35' and r['Exhaust_PWM'] == '100' for r in rows))

    def test_serial_loss_preserves_partial_data_and_stops_fans(self):
        status, rows = self.run_case(fail=True)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('No sensor DATA', status['error'])
        self.assertTrue(rows)


if __name__ == '__main__':
    unittest.main()
