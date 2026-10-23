"""Read-only dashboard notification contracts; no sensor or fan access."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dashboard_api import SnapshotService
from temperature_events import temperature_events


class NotificationTests(unittest.TestCase):
    def rows(self, values):
        return [dict(timestamp=1000.+i, temperature_c=t, intake_rpm=7500., exhaust_rpm=7500.)
                for i,t in enumerate(values)]

    def test_rise_and_recovery(self):
        events = temperature_events(self.rows([24.8,24.9,25.,25.1,25.1,25.1,25.1]))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['trigger'], 'temperature_rise')
        self.assertEqual(events[0]['status'], 'recovered')
        self.assertFalse(events[0]['exceeded_limit'])
        self.assertGreater(events[0]['energy']['energy_wh'], 0)

    def test_gap_is_not_recovery(self):
        events = temperature_events([dict(timestamp=1000.,temperature_c=27.),
                                     dict(timestamp=1010.,temperature_c=25.)])
        self.assertEqual(events[0]['status'], 'interrupted')
        self.assertIsNone(events[0]['recovered_at'])

    def test_normal_stable_has_no_events(self):
        self.assertEqual(temperature_events(self.rows([25.,25.,25.,25.])), [])

    def test_missing_database_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.db'
            result = SnapshotService(path).snapshot(now=1000)
            self.assertFalse(path.exists())
            self.assertTrue(result['stale'])
            self.assertFalse(result['abnormal'])
            self.assertEqual(result['state'], 'unknown')

    def test_db_snapshot_fresh_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sensor_readings(timestamp REAL, temperature_c REAL)')
                db.executemany('INSERT INTO sensor_readings VALUES (?,?)', [(1000.,25.),(1001.,26.1)])
            before=path.read_bytes()
            service=SnapshotService(path)
            fresh=service.snapshot(now=1002)
            self.assertTrue(fresh['abnormal'])
            self.assertEqual(fresh['state'], 'abnormal')
            stale=service.snapshot(now=1010)
            self.assertTrue(stale['stale'])
            self.assertFalse(stale['abnormal'])
            self.assertEqual(stale['state'], 'unknown')
            self.assertEqual(stale['events'][0]['status'], 'interrupted')
            self.assertEqual(before, path.read_bytes())

    def test_invalid_query_window(self):
        with self.assertRaises(ValueError):
            SnapshotService('/missing.db').snapshot(seconds=0)


if __name__=='__main__': unittest.main()
