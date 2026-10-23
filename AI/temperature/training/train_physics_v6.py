"""Refit the default regression baseline using the frozen v6 run split."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))

import argparse
import hashlib
from pathlib import Path

import joblib
import pandas as pd

from project_paths import MODELS_DIR, SPLIT_CSV, TRAINING_CSV
from physics_regression import fit_physics_model


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train(output: Path) -> dict:
    data = pd.read_csv(TRAINING_CSV)
    data['Time'] = pd.to_datetime(data.Time, utc=True, format='mixed')
    manifest = pd.read_csv(SPLIT_CSV)
    if manifest.run.duplicated().any():
        raise ValueError('Each run must occur in exactly one split.')
    if set(data.run) != set(manifest.run):
        raise ValueError('Dataset and split manifest run names differ.')
    if set(manifest.split) != {'train', 'validation', 'test'}:
        raise ValueError('Expected train, validation and test splits.')
    train_runs = set(manifest.loc[manifest.split == 'train', 'run'])
    model = fit_physics_model(data, train_runs)
    model.update(
        horizon_seconds=5,
        training_csv_sha256=sha256(TRAINING_CSV),
        split_csv_sha256=sha256(SPLIT_CSV),
        model_release='v6_split_refit_20261022',
        model_description='Physics-inspired nonnegative linear regression; not a first-principles thermal model.',
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=MODELS_DIR / 'physics_mpc_model.pkl',
                        help='Output model; defaults to overwriting the deployed baseline.')
    args = parser.parse_args()
    model = train(args.output)
    print(f'Saved baseline: {args.output.resolve()}')
    print(model)


if __name__ == '__main__':
    main()
