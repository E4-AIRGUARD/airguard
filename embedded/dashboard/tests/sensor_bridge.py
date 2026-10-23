import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('bridge',Path(__file__).resolve().parents[1]/'integrations/sensor_bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class BridgeTests(unittest.TestCase):
    def test_actual_firmware_frame_and_zero(self):
        row=bridge.parse_data('DATA,4000,0,0,0,0,21.92,35.4,1018.7,0,3,4,0,0',100)
        self.assertEqual(row['temperature_c'],21.92)
        self.assertEqual(row['pm25_ug_m3'],3)
        self.assertEqual(row['intake_rpm'],0)
        self.assertEqual(bridge.power_w(row),0)

    def test_independent_sensor_failure(self):
        row=bridge.parse_data('DATA,4000,35,2700,60,5000,nan,nan,nan,0,3,4,1,0',100)
        self.assertIsNone(row['temperature_c'])
        self.assertEqual(row['pm25_ug_m3'],3)
        row=bridge.parse_data('DATA,4000,35,2700,60,5000,22,40,1018,0,3,4,0,1',100)
        self.assertEqual(row['temperature_c'],22)
        self.assertIsNone(row['pm25_ug_m3'])

    def test_invalid_and_stale(self):
        for bad in ['DATA,1,2','DATA,1,101,0,0,0,22,40,1018,0,3,4,0,0','DATA,1,0,nan,0,0,22,40,1018,0,3,4,0,0']:
            self.assertIsNone(bridge.parse_data(bad))
        state=bridge.Telemetry()
        self.assertFalse(state.snapshot(now=101)['available'])
        state.rows.append(bridge.parse_data('DATA,1,0,0,0,0,22,40,1018,0,3,4,0,0',100))
        self.assertTrue(state.snapshot(now=101)['stale'])
        state.serial_ok=True
        self.assertFalse(state.snapshot(now=101)['stale'])
        self.assertTrue(state.snapshot(now=106)['stale'])

if __name__=='__main__':unittest.main()
