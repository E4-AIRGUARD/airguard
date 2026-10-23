"""Release integrity checks without serial access."""
import hashlib
from pathlib import Path
import sys
import unittest
import joblib
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'training')]
from lstm_controller import LstmController
from train_physics_v6 import train


class ReleaseTests(unittest.TestCase):
    def test_default_model_load(self):
        torch.set_num_threads(1)
        model=LstmController(ROOT/'models/lstm_temperature_trajectory_v6.pt')
        history=np.tile([25.,0.,35.,35.,0.,0.,0.,25.],(8,1))
        self.assertTrue(np.isfinite(model.forecast_current(history)))
        self.assertEqual(model.horizon_seconds,5)

    def test_baseline_reproducible(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            actual=train(Path(tmp)/'physics.pkl')
        self.assertEqual(actual,joblib.load(ROOT/'models/physics_mpc_model.pkl'))

    def test_frozen_data_hashes(self):
        model=joblib.load(ROOT/'models/physics_mpc_model.pkl')
        for relative,key in [('data/processed/heat4_heat6_plus_normal_fan.csv','training_csv_sha256'),
                             ('data/splits/run_manifest.csv','split_csv_sha256')]:
            self.assertEqual(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),model[key])


if __name__=='__main__':unittest.main()
