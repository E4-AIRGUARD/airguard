import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trajectory_lstm_v6 import TrajectoryLSTM
from trajectory_control_v6 import choose_trajectory,TrajectorySwitchGate


class TrajectoryTests(unittest.TestCase):
    def test_artifact_enables_selected_policy_and_assistance(self):
        from main_runner import AirGuardRuntime, build_parser
        args=build_parser().parse_args(['--controller-model','lstm','--lstm-model-file',
            str(ROOT/'models/lstm_temperature_trajectory_v6.pt'),'--database',':memory:','--dummy','--dry-run'])
        runtime=AirGuardRuntime(args)
        try:
            self.assertEqual(runtime.control_policy_version,'v6')
            self.assertIsInstance(runtime.switch_gate,TrajectorySwitchGate)
            self.assertIsNotNone(runtime.raw_rise_assist)
            history=np.tile([23.5,0,35,35,0,0,0,23.5],(8,1))
            detail=runtime.controller.trajectory(history,35,35)
            self.assertEqual(len(detail['temperatures']),3)
            self.assertTrue(np.isfinite(detail['temperatures']).all())
        finally:
            runtime.close()

    def test_model_outputs_are_consistent(self):
        model=TrajectoryLSTM(np.zeros(10),np.ones(10))
        x=torch.zeros(2,8,10)
        delta,rate,logit=model.details(x)
        self.assertEqual(tuple(delta.shape),(2,3))
        self.assertEqual(tuple(logit.shape),(2,))
        torch.testing.assert_close(rate,(delta[:,2]-delta[:,1])/2)
        torch.testing.assert_close(model(x),delta[:,2])

    def test_positive_rate_penalty_can_select_intermediate_pwm(self):
        def trajectory(history,intake,exhaust):
            values={(35,35):[25.6,25.8,25.95],(35,60):[25.5,25.55,25.5],(100,100):[25.35,25.25,25.2]}
            temperatures=np.array(values[(intake,exhaust)])
            slopes=np.diff(np.r_[25.4,temperatures])/np.array([1,2,2])
            return dict(temperatures=temperatures,rate=float(slopes[-1]),max_rate=float(slopes.max()))
        controller=SimpleNamespace(trajectory=trajectory,current_command_pair=(35,35),prediction_margin=np.full(3,.02),
            target_c=26.,rate_weight=25.,display_name='test',pwm_candidates=[(35,35),(35,60),(100,100)])
        result=choose_trajectory(controller,np.array([[25.4,0,35,35,0,0,0,25.4]]),None)
        self.assertEqual((result.intake_pwm,result.exhaust_pwm),(35,60))

    def test_upper_risk_bypasses_economic_wait_only_for_improvement(self):
        current=SimpleNamespace(intake_pwm=35,exhaust_pwm=35,safe=False,upper_temp_c=26.2,
                                predicted_rate_c_per_s=.2,control_cost=1.)
        proposed=SimpleNamespace(intake_pwm=35,exhaust_pwm=60,safe=True,upper_temp_c=25.9,
                                 predicted_rate_c_per_s=.05,control_cost=2.)
        gate=TrajectorySwitchGate()
        self.assertIs(gate.decide(current,proposed,1.,0.,5,upper_risk=True)[0],proposed)
        self.assertIs(gate.decide(current,proposed,1.,0.,5,upper_risk=False)[0],current)

    def test_invalid_predictions_do_not_force_maximum_pwm(self):
        controller=SimpleNamespace(trajectory=lambda *args:dict(temperatures=np.full(3,np.nan),rate=np.nan,max_rate=np.nan),
            current_command_pair=(35,60),prediction_margin=np.zeros(3),target_c=26.,rate_weight=25.,
            display_name='test',pwm_candidates=[(35,35),(35,60),(100,100)])
        result=choose_trajectory(controller,np.array([[25.4,0,35,60,0,0,0,25.4]]),None)
        self.assertEqual((result.intake_pwm,result.exhaust_pwm),(35,60))


if __name__=='__main__':unittest.main()
