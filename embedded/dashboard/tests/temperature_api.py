"""Check the read-only v6 adapter with an isolated fixture, never a live controller."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'integrations/handoff_v6/src'))
import run
import fan_power
from event_store import EventStore


class TemperatureAPI(unittest.TestCase):
    def test_missing_db_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing.db'
            self.assertFalse(run.read_temperature(path, fan_power)['available'])
            self.assertFalse(path.exists())

    def test_records_staleness_power_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            store = EventStore(path)
            self.assertFalse(run.read_temperature(path, fan_power, now=100)['available'])
            for timestamp, rpm in [(100, 7500), (101, 7500), (110, None)]:
                store.sensor(SimpleNamespace(timestamp=timestamp, temperature_c=25.5,
                    humidity_pct=50, pm25_ug_m3=12, heat_load=0, intake_rpm=rpm,
                    exhaust_rpm=rpm, intake_pwm=60, exhaust_pwm=60), 25.5)
            with store.connection:
                store.connection.execute("INSERT INTO control_status (id,timestamp,candidate_json,command_sent) VALUES (1,110,'[]',0)")
            store.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            data = run.read_temperature(path, fan_power, now=111)
            self.assertTrue(data['available'])
            self.assertFalse(data['stale'])
            self.assertFalse(data['control']['stale'])
            self.assertEqual(data['control']['command_sent'], 0)
            self.assertIsNone(data['estimatedPowerW'])
            self.assertAlmostEqual(data['energy']['energy_wh'], 2.7/3600)
            self.assertEqual(data['energy']['excluded_seconds'], 9)
            self.assertTrue(run.read_temperature(path, fan_power, now=120)['stale'])
            self.assertTrue(run.read_temperature(path, fan_power, now=90)['stale'])
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_unrelated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'other.db'
            sqlite3.connect(path).close()
            self.assertFalse(run.read_temperature(path,fan_power)['available'])


if __name__ == '__main__':
    unittest.main()
