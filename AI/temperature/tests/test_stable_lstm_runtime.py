"""모델 입력 호환성 및 실제 온도 조건의 안정화 우회를 검증합니다."""
import sys
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from main_runner import LstmMpcController, normalize_lstm_window
from pwm_stability import PwmStabilityGuard


class StabilityTests(unittest.TestCase):
    def test_legacy_input_matches_original_formula(self):
        model=LstmMpcController(ROOT/'models/lstm_temperature_model_v2.pt',26,35)
        history=np.column_stack([np.linspace(24,24.07,8),np.full(8,.01),np.full(8,35),np.full(8,60)])
        window=np.column_stack([history,np.tile((35,100),(8,1))])
        with torch.inference_mode():
            delta=model.model(torch.tensor(((window-model.mean)/model.std).tolist(),dtype=torch.float32)[None]).item()
        self.assertAlmostEqual(model.forecast(history,35,100),window[-1,0]+delta,places=5)

    def test_stable_model_is_temperature_translation_invariant(self):
        model=LstmMpcController(ROOT/'models/lstm_temperature_stable_v3.pt',26,35)
        history=np.column_stack([np.linspace(23,23.07,8),np.full(8,.01),np.full(8,35),np.full(8,60)])
        shifted=history.copy(); shifted[:,0]+=2
        self.assertAlmostEqual(model.forecast(shifted,35,60)-model.forecast(history,35,60),2,places=4)
        window=np.column_stack([history,np.tile((35,60),(8,1))])
        normalized=normalize_lstm_window(model,window)
        batch=normalize_lstm_window(model,np.stack([window,window]))
        np.testing.assert_allclose(normalized,batch[0],atol=1e-6)

    def test_actual_over_temperature_bypasses_wait(self):
        guard=PwmStabilityGuard()
        chosen,_=guard.decide((35,35),(100,100),26.1,.01,27,0)
        self.assertEqual(chosen,(100,100))

    def test_one_prediction_spike_does_not_change_pwm(self):
        guard=PwmStabilityGuard()
        chosen,_=guard.decide((35,35),(100,100),23,0,27,0)
        self.assertEqual(chosen,(35,35))
        chosen,_=guard.decide((35,35),(35,35),23,0,24,1)
        self.assertEqual(chosen,(35,35))


if __name__=='__main__': unittest.main()
