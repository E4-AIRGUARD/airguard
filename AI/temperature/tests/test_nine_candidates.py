"""All nine candidates must be evaluated, selectable and included in logging."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from lstm_controller import LstmController, PWM_CANDIDATES
from trajectory_control import choose_trajectory
from fan_control import EspFanController


class NineCandidateTests(unittest.TestCase):
    def test_cartesian_product(self):
        self.assertEqual(set(PWM_CANDIDATES), {(a,b) for a in (35,60,100) for b in (35,60,100)})
        self.assertEqual(len(PWM_CANDIDATES), 9)

    def test_new_candidates_can_win_and_all_are_reported(self):
        history=np.tile([25.,0.,35.,35.,0.,0.,0.,25.],(8,1))
        for winner in ((60,35),(100,35),(100,60)):
            with self.subTest(winner=winner):
                controller=Mock(pwm_candidates=PWM_CANDIDATES,current_command_pair=None,
                    prediction_margin=np.zeros(3),target_c=26.,rate_weight=25.,display_name='test')
                def trajectory(history,intake,exhaust):
                    temp=25. if (intake,exhaust)==winner else 27.
                    return dict(temperatures=np.array([temp]*3),rate=0.,max_rate=0.)
                controller.trajectory=Mock(side_effect=trajectory)
                result=choose_trajectory(controller,history,PWM_CANDIDATES)
                self.assertEqual((result.intake_pwm,result.exhaust_pwm),winner)
                self.assertEqual(controller.trajectory.call_count,9)
                self.assertEqual(len(result.all_candidates),9)
                self.assertEqual(len(result.candidate_comparison),9)
                self.assertEqual(sum(c.selected for c in result.candidate_comparison),1)

    def test_shipped_model_evaluates_nine(self):
        model=LstmController(ROOT/'models/lstm_temperature_trajectory_v6.pt')
        history=np.tile([25.,0.,35.,35.,0.,0.,0.,25.],(8,1))
        result=model.choose(history)
        self.assertEqual(len(result.all_candidates),9)
        self.assertEqual(len(result.candidate_comparison),9)

    def test_fan_driver_accepts_intake_dominant_pairs(self):
        fan=EspFanController(dry_run=True)
        for pair in ((60,35),(100,35),(100,60)):
            command=fan.apply(*pair,'nine candidate test')
            self.assertEqual((command.intake_percent,command.exhaust_percent),pair)


if __name__=='__main__':unittest.main()
