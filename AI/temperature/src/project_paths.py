"""Paths anchored to this project, not the caller's working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_HEAT_DIR = PROJECT_ROOT / 'data/raw/heat4-6'
NORMAL_SOURCE = PROJECT_ROOT / 'data/raw/training_data_v2.csv'
TRAINING_CSV = PROJECT_ROOT / 'data/processed/heat4_heat6_plus_normal_fan.csv'
SPLIT_CSV = PROJECT_ROOT / 'data/splits/run_manifest.csv'
MODELS_DIR = PROJECT_ROOT / 'models'
RESULTS_DIR = PROJECT_ROOT / 'results'
