import csv
import json
import tempfile
import unittest
from pathlib import Path

from measure_heat_matrix import HeatSession, ready, COMBINATIONS
from measure_thermal_response import CSV_HEADER


def sample(temp=24, pm=0, duty=0, rpm=0, **extra):
    return {'temp_c': temp, 'rh_pct': 54, 'pm2_5': pm,
            'duty_in': duty, 'duty_out': duty, 'rpm_in': rpm, 'rpm_out': rpm,
            'bme_err': 0, 'pms_stale': 0, **extra}


class HeatMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 0
        self.commands = []
        self.session = HeatSession(self.root, self.commands.append, lambda: self.now)
        self.addCleanup(self.session.close_csv)

    def advance(self, now, d):
        self.now = now
        self.session.receive(d)
        self.session.tick()

    def begin_heat(self):
        self.advance(0, sample())
        self.assertTrue(self.session.start())
        self.advance(5, sample())
        self.assertEqual(self.session.stage, 'heating')

    def test_temperature_only_inclusive_with_fresh_sensor_and_stopped_fans(self):
        for temp in (22, 24, 26):
            for pm in (0, 1, 19, 300):
                self.assertTrue(ready(sample(temp, pm), 1))
        self.assertTrue(ready(sample(pms_stale=1), 1))
        for d, age in ((sample(21.99), 0), (sample(26.01), 0), (sample(float('nan')), 0),
                       (sample(bme_err=1), 0), (sample(rpm=100), 0), (sample(duty=35), 0),
                       (sample(), 2.51), (None, 0)):
            self.assertFalse(ready(d, age))

    def test_wait_countdown_heat_cooling_and_manual_next_preserve_schema(self):
        self.begin_heat()
        self.assertEqual(self.commands, [])
        for t in range(6, 11):
            self.advance(t, sample(temp=24 + t))
        self.assertEqual(self.commands, ['F 35 35'])
        self.assertEqual(self.session.stage, 'cooling')
        for t in range(11, 311):
            self.advance(t, sample(temp=30, duty=35, rpm=1000))
        self.assertEqual(self.commands, ['F 35 35', '0'])
        self.assertEqual(self.session.stage, 'stopping')
        self.advance(311, sample(rpm=50))
        self.assertEqual(self.session.completed, [])
        self.advance(312, sample())
        self.assertEqual(len(self.session.completed), 1)
        self.assertEqual(self.session.stage, 'waiting')
        self.advance(400, sample())
        self.assertEqual(self.commands, ['F 35 35', '0'])
        with next((self.root / 'csv').glob('*.csv')).open(encoding='utf-8') as f:
            reader = csv.DictReader(f)
            self.assertEqual(reader.fieldnames, CSV_HEADER)
            rows = list(reader)
        self.assertEqual(len(rows), 305)
        self.assertEqual(sum(r['Heat_Load'] == '1' for r in rows), 5)
        self.assertTrue(all(r['Intake_PWM'] == '35' for r in rows[5:]))
        events = [json.loads(s) for s in (self.root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(e['event'] == 'combination_complete' and e['stop_verified'] for e in events))

    def test_recheck_temperature_at_end_of_countdown(self):
        self.advance(0, sample())
        self.session.start()
        self.advance(5, sample(temp=27))
        self.assertEqual(self.session.stage, 'waiting')
        self.assertEqual(list((self.root / 'csv').glob('*.csv')), [])

    def test_stale_serial_during_heat_fails(self):
        self.begin_heat()
        self.now = 11
        with self.assertRaisesRegex(OSError, 'DATA'):
            self.session.tick()

    def test_missing_rpm_does_not_complete_condition(self):
        self.begin_heat()
        self.advance(10, sample(30))
        with self.assertRaisesRegex(OSError, 'RPM'):
            self.advance(19, sample(30, duty=35))
        self.assertEqual(self.session.completed, [])

    def test_stale_pm_is_blank_and_does_not_block_heat_recording(self):
        self.begin_heat()
        self.advance(6, sample(30, pms_stale=1))
        self.session.close_csv()
        with next((self.root / 'csv').glob('*.csv')).open(encoding='utf-8') as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row['PM2.5'], '')
        self.assertEqual(row['Heat_Load'], '1')

    def test_failed_stop_does_not_advance(self):
        self.session.stage = 'stopping'
        self.session.deadline = 10
        self.now = 10
        with self.assertRaisesRegex(OSError, '정지'):
            self.session.tick()
        self.assertEqual(self.session.index, 0)

    def test_all_nine_pairs_finish_in_order_without_automatic_restart(self):
        for index, (name, intake, exhaust) in enumerate(COMBINATIONS):
            self.session.receive(sample())
            self.assertTrue(self.session.start())
            for second in range(1, 311):
                self.now += 1
                self.session.tick()
                d = sample() if self.session.stage in ('waiting', 'countdown', 'stopping') else sample(temp=35)
                if self.session.stage == 'cooling':
                    d.update(duty_in=intake, duty_out=exhaust, rpm_in=1000, rpm_out=1000)
                self.session.receive(d)
            self.assertEqual(self.session.completed[-1], name)
            self.assertEqual(self.session.index, index + 1)
            self.assertEqual(self.session.stage, 'complete' if index == 8 else 'waiting')
        self.assertEqual(self.commands, [cmd for _, i, o in COMBINATIONS for cmd in (f'F {i} {o}', '0')])
        self.assertEqual(len(list((self.root / 'csv').glob('*.csv'))), 9)

    def test_camera_only_records_after_heat_until_fan_measurement_end(self):
        captures = []
        self.session.begin_capture = lambda: captures.append(('begin', self.now))
        self.session.end_capture = lambda: captures.append(('pause', self.now))
        self.begin_heat()
        self.assertEqual(captures, [])
        self.advance(9, sample(35))
        self.assertEqual(captures, [])
        self.advance(10, sample(35))
        self.assertEqual(captures, [('begin', 10)])
        for t in range(11, 311):
            self.advance(t, sample(30, duty=35, rpm=1000))
        self.assertEqual(captures, [('begin', 10), ('pause', 310)])
        self.advance(312, sample())
        self.advance(600, sample())
        self.assertEqual(len(captures), 2)


if __name__ == '__main__':
    unittest.main()
