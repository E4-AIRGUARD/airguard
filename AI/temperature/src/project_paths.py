"""Paths anchored to this project, not the caller's working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_CSV = PROJECT_ROOT / 'data/processed/heat4_heat6_plus_normal_fan.csv'
SPLIT_CSV = PROJECT_ROOT / 'data/splits/run_manifest.csv'
MODELS_DIR = PROJECT_ROOT / 'models'
RESULTS_DIR = PROJECT_ROOT / 'results'
