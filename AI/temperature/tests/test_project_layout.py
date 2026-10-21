"""Check submission inputs without historical study directories."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from project_paths import TRAINING_CSV, SPLIT_CSV, RAW_HEAT_DIR, MODELS_DIR


class ProjectLayoutTests(unittest.TestCase):
    def test_all_runs_have_exactly_one_split(self):
        data = pd.read_csv(TRAINING_CSV)
        manifest = pd.read_csv(SPLIT_CSV)
        self.assertFalse(manifest.run.duplicated().any())
        self.assertEqual(set(data.run), set(manifest.run))
        self.assertEqual(set(manifest.split), {'train', 'validation', 'test'})
        self.assertEqual(len(manifest), 36)

    def test_raw_data_and_deployment_models_exist(self):
        self.assertEqual(len(list(RAW_HEAT_DIR.glob('*.csv'))), 27)
        for name in ('physics_mpc_model.pkl', 'lstm_temperature_model.pt',
                     'lstm_temperature_model_v2.pt', 'lstm_temperature_stable_v3.pt',
                     'lstm_temperature_multirate_v4.pt'):
            self.assertTrue((MODELS_DIR / name).is_file(), name)
