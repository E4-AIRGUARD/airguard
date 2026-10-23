from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integrations'))
import integrated_control
from integration_store import IntegrationStore
from control_types import InferenceResult


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'events.db';self.store=IntegrationStore(self.path)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def feed(self,t,temp=22,bme=True):
        row=dict(timestamp=t,temperature_c=temp,humidity_pct=40,pm25_ug_m3=20,intake_rpm=2700,exhaust_rpm=2900,
                 intake_pwm=35,exhaust_pwm=35,bme_ok=bme,pms_ok=True,pm1_ug_m3=5,pm10_ug_m3=25,pressure_hpa=1019)
        result=dict(timestamp=t,mode='live',commandSent=True,proposed=dict(intake=60,exhaust=100),
            smokeDemand=dict(reason='pm'),temperatureDemand=dict(reason='temperature'),lastSent=[60,100])
        decision=None if temp is None else (SimpleNamespace(timestamp=t,temperature_c=temp),
            InferenceResult(temp+.1,35,60,20,'LSTM v6'),'temperature')
        self.store.record(dict(sensor=row,history=[row]),result,decision)
    def test_final_command_distinct_from_temperature_demand(self):
        self.feed(100);d=self.store.snapshot(now=100)
        self.assertEqual(d['control']['intake_percent'],35)
        self.assertEqual(d['control']['command_sent'],0)
        self.assertEqual(d['integratedControl']['proposed']['intake'],60)
        self.assertTrue(d['controlEvents'][0]['command_sent'])
        self.assertEqual(d['sensor']['pm10_ug_m3'],25)
        self.assertEqual(d['predictedTemperatureC'],22.1)
    def test_sensor_error_not_normal_and_stale_hides_targets(self):
        self.feed(100);self.feed(101,None,False)
        d=self.store.snapshot(now=101)
        self.assertEqual(d['temperatureAlert']['state'],'unknown')
        self.assertIsNone(d['predictedTemperatureC'])
        self.assertEqual(d['sensor']['pm25_ug_m3'],20)
        d=self.store.snapshot(now=112)
        self.assertTrue(d['stale']);self.assertIsNone(d['integratedControl'])
        self.assertIsNone(d['control'])
    def test_persistent_recovery_and_no_double_energy(self):
        for t in range(100,104):self.feed(t,27)
        for t in range(104,109):self.feed(t,25)
        self.store.close();self.store=IntegrationStore(self.path)
        d=self.store.snapshot(now=108)
        self.assertEqual(d['incidents'][0]['status'],'recovered')
        self.assertEqual(d['energy']['covered_seconds'],8)
        self.assertEqual(len(d['history']),9)
    def test_stale_incident_not_recovery(self):
        self.feed(100,27)
        d=self.store.snapshot(now=108)
        self.assertEqual(d['incidents'][0]['status'],'unconfirmed')
        self.assertIsNone(d['incidents'][0]['end'])

if __name__=='__main__':unittest.main()
