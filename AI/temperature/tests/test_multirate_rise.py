"""원본 변화율, 보조 냉각, 다중 입력의 런타임 일치를 검증합니다."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from main_runner import AirGuardRuntime,LstmMpcController,build_parser,normalize_lstm_window
from raw_rise_assist import RawRiseAssist
from sensor_buffer import SensorBuffer,SensorSample
from temperature_features import raw_temperature_rates


class MultiRateTests(unittest.TestCase):
    def test_rates_use_actual_elapsed_time(self):
        timestamps=np.array([0,1.02,2.01,3.03,4.01,5.02,6.03,7.01])
        rates=raw_temperature_rates(24+timestamps*.2,timestamps)
        np.testing.assert_allclose(rates[-1],[.2,.2,.2],atol=1e-5)

    def test_gap_does_not_invent_a_trend(self):
        rates=raw_temperature_rates([24,24,26],[0,1,6])
        np.testing.assert_array_equal(rates[-1],[0,0,0])

    def test_duplicate_does_not_advance_release_count(self):
        assist=RawRiseAssist();samples=[SensorSample(i,24.9+.1*i) for i in range(5)]
        self.assertTrue(assist.update(samples).active)
        samples.append(SensorSample(5,25.3));assist.update(samples)
        count=assist.clears
        for _ in range(10):assist.update(samples)
        self.assertEqual(assist.clears,count)

    def test_small_sensor_noise_does_not_trigger(self):
        assist=RawRiseAssist();samples=[]
        for i in range(30):
            samples.append(SensorSample(i,25.+.01*(-1)**i))
            self.assertFalse(assist.update(samples[-8:]).active)

    def test_buffer_legacy_columns_are_preserved(self):
        buffer=SensorBuffer(8)
        for i in range(8):buffer.append(SensorSample(i,24.+.1*i,intake_pwm=35,exhaust_pwm=60))
        old=buffer.control_feature_matrix(35,60)
        new=buffer.control_feature_matrix(35,60,include_raw_rates=True)
        self.assertEqual(new.shape,(8,7));np.testing.assert_array_equal(old,new[:,:4])
        np.testing.assert_allclose(new[-1,4:],[.1,.1,.1],atol=1e-5)

    def test_new_model_single_and_candidate_batch_match(self):
        model=LstmMpcController(ROOT/'models/lstm_temperature_multirate_v4.pt',26,35)
        buffer=SensorBuffer(8)
        for i in range(8):buffer.append(SensorSample(i,24.+.01*i,intake_pwm=35,exhaust_pwm=35))
        history=buffer.control_feature_matrix(35,35,include_raw_rates=True)
        window=np.column_stack((history,np.tile((35,60),(8,1))))
        batch=np.stack([window,window])
        with torch.inference_mode():d=model.model(torch.tensor(normalize_lstm_window(model,batch))).numpy()
        self.assertAlmostEqual(model.forecast(history,35,60),float(history[-1,0]+d[0]),places=5)
        result=model.choose(history)
        self.assertTrue(np.isfinite(result.predicted_temp_c))

    def test_raw_assist_runs_before_model_warmup(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=build_parser().parse_args(['--dummy','--dry-run','--controller-model','lstm','--raw-rise-assist',
                '--models',str(ROOT/'models'),'--lstm-model-file','lstm_temperature_stable_v3.pt',
                '--database',str(Path(tmp)/'test.db')])
            runtime=AirGuardRuntime(args);runtime.period=.001
            now=time.time()
            for i in range(5):runtime.receive_sample(SensorSample(now-4+i,24.9+.1*i))
            captured=[]
            def apply(sample,result,reason):
                captured.append(result);runtime.stop_event.set()
            runtime._apply=apply
            try:runtime.run()
            finally:runtime.close()
            self.assertFalse(runtime.buffer.is_ready())
            self.assertTrue(captured[0].model_name.startswith('Raw rise assist'))
            self.assertEqual((captured[0].intake_pwm,captured[0].exhaust_pwm),(35,60))


if __name__=='__main__':unittest.main()
