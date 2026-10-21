import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from control_policy_v5 import HysteresisPolicy,SafeEnergySwitchGate
from main_runner import AirGuardRuntime,CandidateComparison,PhysicsMpcController,build_parser


def candidate(pair=(35,35),prediction=25.4,safe=True,recovery=None):
    power=sum((v/100)**3 for v in pair)
    return CandidateComparison(*pair,prediction,power,safe,recovery_seconds=recovery,
                               total_relative_energy=power*recovery if recovery is not None else None)


class PolicyV5Tests(unittest.TestCase):
    def test_v5_artifact_selects_v5_policy_automatically(self):
        root=Path(__file__).resolve().parents[1]
        args=build_parser().parse_args(['--controller-model','lstm','--lstm-model-file',
            str(root/'models/lstm_temperature_multirate_v5.pt'),'--database',':memory:','--dummy','--dry-run'])
        runtime=AirGuardRuntime(args)
        try:
            self.assertEqual(runtime.control_policy_version,'v5')
            self.assertIsInstance(runtime.switch_gate,SafeEnergySwitchGate)
        finally:runtime.close()
    def test_hysteresis_does_not_switch_back_at_entry_boundary(self):
        policy=HysteresisPolicy()
        self.assertEqual(policy.update(25.4,25.55)[0],'상한 보호')
        self.assertEqual(policy.update(25.4,25.45)[0],'상한 보호')
        self.assertEqual(policy.update(25.2,25.29)[0],'예방 냉각')
        self.assertEqual(policy.update(24.9,24.99)[0],'예방 냉각')
        self.assertEqual(policy.update(24.7,24.79)[0],'절전 유지')

    def test_current_command_remains_in_comparison_candidates(self):
        runtime=AirGuardRuntime.__new__(AirGuardRuntime)
        runtime.control_policy_version='v5';runtime.policy_v5=HysteresisPolicy()
        runtime.state=SimpleNamespace(last_intake_percent=35,last_exhaust_percent=35)
        runtime.controller=SimpleNamespace(forecast_current=lambda h:25.7,pwm_candidates=[(35,35),(35,60),(35,100),(100,100)])
        result=runtime._control_policy(np.array([[25.6,0,35,35]]))
        self.assertIn((35,35),result[3])

    def test_unknown_recovery_does_not_bypass_hold(self):
        gate=SafeEnergySwitchGate()
        current=candidate();proposed=candidate((100,100),25.2,True,10)
        result,reason=gate.decide(current,proposed,1,0,5)
        self.assertIs(result,current)
        self.assertIn('회복시간 불확실',reason)

    def test_internal_target_failure_is_not_an_emergency(self):
        gate=SafeEnergySwitchGate()
        current=candidate(prediction=26.01,safe=False)
        proposed=candidate((100,100),25.8,True,0)
        self.assertIs(gate.decide(current,proposed,1,0,5,upper_risk=False)[0],current)
        self.assertIs(gate.decide(current,proposed,1,0,5,upper_risk=True)[0],proposed)

    def test_recovery_failure_does_not_force_maximum_pwm(self):
        model=PhysicsMpcController.__new__(PhysicsMpcController)
        model.model=dict(ambient_temp_c=24,intercept=.01,env_gain=0,trend_gain=0,fan_gain=0)
        model.target_c=26;model.horizon_seconds=5;model.control_policy_version='v5'
        model.current_command_pair=(35,35);model.pwm_candidates=[(35,35),(100,100)]
        result=model.choose(np.array([[25.4,0,35,35]]),25.)
        self.assertEqual((result.intake_pwm,result.exhaust_pwm),(35,35))
        self.assertIsNone(result.recovery_seconds)

    def test_upper_risk_requires_consecutive_forecasts_or_actual_limit(self):
        policy=HysteresisPolicy()
        self.assertFalse(policy.upper_risk(25.6,26.1))
        self.assertTrue(policy.upper_risk(25.6,26.1))
        self.assertFalse(policy.upper_risk(25.6,25.9))
        self.assertTrue(policy.upper_risk(26.,25.9))


if __name__=='__main__':unittest.main()
