from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integrations'))
from integrated_control import Coordinator,ActualEnergy,SmokeFan,valid_pair


def sample(t=100,pm=20):
    return dict(available=True,stale=False,sensor=dict(timestamp=t,temperature_c=22.,
        humidity_pct=40.,pm25_ug_m3=pm,bme_ok=True,pms_ok=True,
        intake_pwm=0,exhaust_pwm=0,intake_rpm=7500,exhaust_rpm=7500))


class Temperature:
    def __init__(self,pair=(60,100)):
        self.current_pair=(-1,-1);self.last_pwm_change=0;self.confirmed=[];self.samples=[];self.pair=pair
    def observe(self,s):self.samples.append(s)
    def decide(self,**kwargs):
        return None if not self.samples else (self.samples[-1],SimpleNamespace(intake_pwm=self.pair[0],exhaust_pwm=self.pair[1],predicted_temp_c=24.,model_name='test'), 'test temperature demand')
    def confirm_applied(self,*pair,**kwargs):self.confirmed.append(pair);self.current_pair=pair


class IntegrationTests(unittest.TestCase):
    def test_shadow_no_confirmation_and_per_leg_max(self):
        temp=Temperature();smoke=SmokeFan()
        c=Coordinator(temp,smoke,SimpleNamespace)
        result=c.step(sample(),wall=100,clock=100)
        self.assertEqual(result['proposed'],dict(intake=60,exhaust=100))
        self.assertEqual(temp.confirmed,[])
        self.assertFalse(result['commandSent'])
        self.assertEqual(result['mode'],'shadow')
        self.assertEqual(result['feedback'],[0,0])
        self.assertFalse(result['adaptationActive'])

    def test_duplicate_samples_do_not_refresh_pm_or_temperature(self):
        temp=Temperature();c=Coordinator(temp,SmokeFan(),SimpleNamespace)
        c.step(sample(),wall=100,clock=100)
        c.step(sample(),wall=101,clock=101)
        result=c.step(sample(),wall=111,clock=111)
        self.assertEqual(len(temp.samples),1)
        self.assertEqual(result['smokeDemand']['reason'],'sensor_invalid_or_stale')
        self.assertEqual(result['proposed'],dict(intake=100,exhaust=100))
        self.assertFalse(result['sensorFresh'])

    def test_real_write_success_confirms_final_only(self):
        calls=[];temp=Temperature();c=Coordinator(temp,SmokeFan(),SimpleNamespace,lambda *p:calls.append(p))
        result=c.step(sample(),wall=100,clock=100)
        self.assertTrue(result['commandSent'])
        self.assertEqual(calls,[(60,100)]);self.assertEqual(temp.confirmed,calls)
        c.step(sample(101),wall=101,clock=101)
        self.assertEqual(len(calls),1)
        c.step(sample(105),wall=105,clock=105)
        self.assertEqual(len(calls),2)

    def test_write_failure_not_confirmed(self):
        def fail(*args):raise OSError('serial failed')
        temp=Temperature();c=Coordinator(temp,SmokeFan(),SimpleNamespace,fail)
        with self.assertRaises(OSError):c.step(sample(),wall=100,clock=100)
        self.assertEqual(temp.confirmed,[]);self.assertIsNone(c.last_sent)

    def test_energy_once_and_gap_excluded(self):
        meter=ActualEnergy()
        for t in [100,100,101,110]:meter.observe(sample(t)['sensor'])
        self.assertAlmostEqual(meter.wh,2.7/3600)
        self.assertEqual(meter.covered,1);self.assertEqual(meter.excluded,9)

    def test_no_silent_pwm_rounding(self):
        for pair in [(34,60),(35,101),(float('nan'),35)]:
            with self.assertRaises(ValueError):valid_pair(pair)

    def test_actual_temperature_v6_api(self):
        from sensor_buffer import SensorSample
        from temperature_controller import TemperatureController
        root=Path(__file__).resolve().parents[1]/'integrations'
        temp=TemperatureController(root/'handoff_v6/models/lstm_temperature_trajectory_v6.pt')
        c=Coordinator(temp,SmokeFan(),SensorSample)
        for t in range(100,110):
            result=c.step(sample(t),wall=t,clock=t)
        self.assertEqual(result['mode'],'shadow')
        self.assertFalse(result['commandSent'])
        valid_pair(tuple(result['proposed'].values()))
        self.assertIn('LSTM v6',result['temperatureDemand']['model'])


if __name__=='__main__':unittest.main()
