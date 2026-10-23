from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
import importlib.util
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integrations'))
from dashboard_lease import DashboardLease,FanLeaseGuard
from integrated_control import Coordinator,SmokeFan


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.now=100.;self.lease=DashboardLease(15,lambda:self.now)
        self.a='a'*16;self.b='b'*16
        self.calls=[];self.guard=FanLeaseGuard(self.lease,lambda *pair:self.calls.append(pair))
    def test_last_tab_close_only(self):
        for client in (self.a,self.b):self.lease.update(client,'heartbeat',1)
        self.lease.update(self.a,'close',2)
        self.assertTrue(self.guard.send(35,60));self.assertEqual(self.calls[-1],(35,60))
        self.lease.update(self.b,'close',2)
        self.guard.check();self.assertEqual(self.calls[-1],(0,0))
        self.assertFalse(self.guard.send(100,100));self.assertEqual(self.calls[-1],(0,0))
    def test_deadman_runs_without_control_inference(self):
        self.lease.update(self.a,'heartbeat',1);self.guard.send(35,35)
        self.now=114.9;self.guard.check();self.assertEqual(self.calls[-1],(35,35))
        self.now=115;self.guard.check();self.assertEqual(self.calls[-1],(0,0))
    def test_late_heartbeat_cannot_undo_close(self):
        self.lease.update(self.a,'close',3)
        self.lease.update(self.a,'heartbeat',1)
        self.lease.update(self.a,'heartbeat',4)
        self.assertFalse(self.lease.status()['active'])
        self.lease.update(self.b,'heartbeat',1)
        self.assertTrue(self.lease.status()['active'])
    def test_old_sequence_does_not_extend_timeout(self):
        self.lease.update(self.a,'heartbeat',2);self.now=110
        self.lease.update(self.a,'heartbeat',1);self.now=115
        self.assertFalse(self.lease.status()['active'])
    def test_no_tab_never_drives_fan(self):
        self.assertFalse(self.guard.send(35,35));self.assertEqual(self.calls,[(0,0)])
    def test_invalid_client_rejected(self):
        for client in ('','x','../../a',None):
            with self.assertRaises(ValueError):self.lease.update(client,'heartbeat',1)
    def test_coordinator_paused_and_resume(self):
        class Temperature:
            current_pair=(0,0);last_pwm_change=0
            def observe(self,s):pass
            def decide(self,**kwargs):return None
            def confirm_applied(self,*pair,**kwargs):self.current_pair=pair
        c=Coordinator(Temperature(),SmokeFan(),SimpleNamespace,self.guard.send)
        data=dict(available=True,stale=False,sensor=dict(timestamp=100,temperature_c=22,pm25_ug_m3=10,
            intake_pwm=0,exhaust_pwm=0,intake_rpm=0,exhaust_rpm=0))
        paused=c.step(data,wall=100,clock=100,enabled=False)
        self.assertEqual(paused['mode'],'paused');self.assertEqual(c.last_sent,(0,0))
        self.lease.update(self.a,'heartbeat',1)
        live=c.step(data,wall=101,clock=101)
        self.assertEqual(live['mode'],'live');self.assertEqual(c.last_sent,(35,35))
        self.lease.update(self.a,'close',2)
        denied=c.step(data,wall=107,clock=107)
        self.assertEqual(denied['mode'],'paused');self.assertEqual(c.last_sent,(0,0))

if __name__=='__main__':unittest.main()
