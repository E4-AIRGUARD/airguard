import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import check_smoke_start as check
import measure_smoke_after_manual as smoke


class FakeSerial:
    def __init__(self, pm=500, failure=None):
        self.now = 0
        self.is_open = False
        self.pair = (0, 0)
        self.pm = pm
        self.failure = failure
        self.writes = []

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass

    def write(self, value):
        self.writes.append(value)
        self.pair = tuple(map(int, value.split()[1:])) if value.startswith(b'F ') else (0, 0)

    def readline(self):
        self.now += 1
        if self.failure == 'serial' and self.now > 10 and any(self.pair):
            return b''
        stale = int(self.failure == 'stale' and self.now > 10)
        i, o = self.pair
        return (f'DATA,{self.now * 1000},{i},{1000 if i else 0},{o},{2000 if o else 0},'
                f'-99,-99,-99,0,{self.pm},0,1,{stale}\n').encode()


class SmokeTests(unittest.TestCase):
    def test_status_recovers_from_temporary_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text('{"rows": 96}')
            replace = Path.replace
            attempts = []

            def transient_lock(source, target):
                attempts.append(target)
                if len(attempts) < 3:
                    raise PermissionError('file locked')
                return replace(source, target)

            with patch.object(Path, 'replace', transient_lock), patch.object(smoke.time, 'sleep'):
                smoke.write_status(path, {'rows': 97})
            self.assertEqual(json.loads(path.read_text()), {'rows': 97})
            self.assertEqual(len(attempts), 3)

    def test_persistent_status_lock_preserves_previous_state_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text('{"rows": 96}')
            with patch.object(Path, 'replace', side_effect=PermissionError('file locked')) as replace, \
                    patch.object(smoke.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    smoke.write_status(path, {'rows': 97})
            self.assertEqual(replace.call_count, 11)
            self.assertEqual(json.loads(path.read_text()), {'rows': 96})

    def baseline(self, root):
        path = root / 'baseline.json'
        report = dict(combination=1, ready=True, checked_at=datetime.now().astimezone().isoformat(),
                      last={'pm2_5': 7, 'pms_stale': 0})
        path.write_text(json.dumps(report), encoding='utf-8')
        return path, report

    def run_case(self, failure=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, _ = self.baseline(root)
            port = FakeSerial(failure=failure)
            camera = Mock(count=300)
            if failure == 'camera':
                camera.check.side_effect = OSError('camera disconnected')
            with patch.object(smoke.serial, 'Serial', return_value=port), \
                    patch.object(smoke, 'CameraRecorder', return_value=camera), \
                    patch.object(smoke.time, 'monotonic', side_effect=lambda: port.now), \
                    contextlib.redirect_stdout(io.StringIO()):
                args = ['--session-dir', str(root), '--combination', '1', '--pre-smoke-check', str(baseline)]
                if failure:
                    with self.assertRaises(SystemExit):
                        smoke.main(args)
                else:
                    smoke.main(args)
            run = root / '01_intake_weak_exhaust_weak'
            status = json.loads((run / 'status.json').read_text(encoding='utf-8'))
            with next(run.glob('*.csv')).open(encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, smoke.CSV_HEADER)
                rows = list(reader)
            self.assertFalse(port.is_open)
            self.assertEqual(port.writes[0], b'F 35 35\n')
            self.assertEqual(port.writes[-1], b'0\n')
            self.assertTrue(status['stop_verified'])
            camera.begin.assert_called_once()
            camera.close.assert_called_once()
            return status, rows

    def test_high_smoke_pm_runs_300_seconds_without_bme(self):
        status, rows = self.run_case()
        self.assertEqual(status['status'], 'complete')
        self.assertEqual(status['elapsed_sec'], 300)
        self.assertTrue(rows)
        self.assertTrue(all(r['Temp'] == r['Humidity'] == '' and r['Heat_Load'] == '0'
                            and r['PM2.5'] == '500' and r['Intake_PWM'] == r['Exhaust_PWM'] == '35'
                            for r in rows))
        self.assertNotIn('temp_c', status['last_sensor'])

    def test_serial_loss_preserves_partial_data_and_stops(self):
        status, rows = self.run_case('serial')
        self.assertEqual(status['status'], 'failed')
        self.assertIn('No sensor DATA', status['error'])
        self.assertTrue(rows)

    def test_stale_pm_stops_run(self):
        status, _ = self.run_case('stale')
        self.assertEqual(status['status'], 'failed')
        self.assertIn('stale', status['error'])

    def test_camera_error_stops_run(self):
        status, _ = self.run_case('camera')
        self.assertEqual(status['status'], 'failed')
        self.assertIn('camera disconnected', status['error'])

    def test_invalid_baseline_or_stop_never_opens_hardware(self):
        for variant in ('pm_low', 'pm_high', 'stale', 'pair', 'expired', 'stop'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path, report = self.baseline(root)
                if variant == 'pm_low':
                    report['last']['pm2_5'] = -1
                elif variant == 'pm_high':
                    report['last']['pm2_5'] = 21
                elif variant == 'stale':
                    report['last']['pms_stale'] = 1
                elif variant == 'pair':
                    report['combination'] = 2
                elif variant == 'expired':
                    report['checked_at'] = (datetime.now().astimezone() - timedelta(minutes=16)).isoformat()
                else:
                    (root / 'STOP').touch()
                path.write_text(json.dumps(report), encoding='utf-8')
                with patch.object(smoke.serial, 'Serial') as serial_factory, \
                        patch.object(smoke, 'CameraRecorder') as camera_factory:
                    with self.assertRaises(SystemExit):
                        smoke.main(['--session-dir', str(root), '--combination', '1', '--pre-smoke-check', str(path)])
                    serial_factory.assert_not_called()
                    camera_factory.assert_not_called()

    def test_baseline_check_only_reads_and_ignores_disabled_bme(self):
        for pm, stale, ready in ((0, False, True), (17, False, True), (20, False, True), (7, False, True),
                                 (-1, False, False), (21, False, False), (18, True, False)):
            with self.subTest(pm=pm, stale=stale), tempfile.TemporaryDirectory() as tmp:
                port = FakeSerial(pm=pm, failure='stale' if stale else None)
                port.now = 10
                with patch.object(check.serial, 'Serial', return_value=port), \
                        patch.object(check.time, 'monotonic', side_effect=lambda: port.now), \
                        patch.object(smoke, 'CameraRecorder') as camera_factory, \
                        contextlib.redirect_stdout(io.StringIO()):
                    check.main(['--session-dir', tmp, '--combination', '1'])
                report = json.loads(next((Path(tmp) / 'start_checks').glob('*.json')).read_text())
                self.assertEqual(report['ready'], ready)
                self.assertTrue(report['fans_stopped'])
                self.assertNotIn('temp_c', report['last'])
                self.assertEqual(port.writes, [])
                self.assertFalse(port.is_open)
                camera_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
