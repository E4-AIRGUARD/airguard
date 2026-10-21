"""Train runtime-compatible Physics and LSTM models from 4 s and 6 s heat runs only.

The source CSVs stay unchanged. A run is the unit of splitting, so augmented
examples never enter validation or test data. The generated .pkl and .pt files
use the same format expected by main_runner.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch

from train_model import (
    MODEL_VERSION,
    TARGET_TEMP_C,
    arrays,
    build_examples,
    evaluate,
    evaluate_observed_control,
    fit_physics_model,
    frame_for,
    physics_forecast,
    plot,
    train_lstm,
)


from project_paths import RAW_HEAT_DIR, RESULTS_DIR

DEFAULT_SOURCE = RAW_HEAT_DIR
DEFAULT_OUTPUT = RESULTS_DIR / 'heat4_heat6_baseline'
DEFAULT_HORIZON_SECONDS = 10
SEQUENCE_LENGTHS = (4, 6, 8)


def source_runs(source: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load only the known 4 s/6 s collection and assign a fixed run split."""
    files = sorted(source.glob("*.csv"))
    if len(files) != 27:
        raise ValueError(f"Expected 27 source runs under {source}, found {len(files)}.")

    records: list[dict] = []
    frames: list[pd.DataFrame] = []
    for path in files:
        run_id = path.stem
        # The first 2026-10-18 collection uses the 'copy 2' filenames and is 4 s.
        duration = 4 if run_id.endswith("copy 2") else 6
        data = pd.read_csv(path, parse_dates=["Time"])
        missing = {"Time", "Temp", "Intake_PWM", "Exhaust_PWM"} - set(data)
        if missing:
            raise ValueError(f"{path.name}: missing required columns {sorted(missing)}")
        for column in ("Temp", "Intake_PWM", "Exhaust_PWM"):
            data[column] = pd.to_numeric(data[column], errors="raise")
        if data.Time.isna().any() or data.Time.duplicated().any() or not data.Time.is_monotonic_increasing:
            raise ValueError(f"{path.name}: Time must be valid, unique, and ascending.")
        if not data[["Intake_PWM", "Exhaust_PWM"]].isin(range(101)).all().all():
            raise ValueError(f"{path.name}: PWM must be an integer between 0 and 100.")

        combo = int(run_id[:2])
        repeat = 1 if duration == 4 else (2 if " copy" not in run_id else 3)
        records.append(
            {
                "run": run_id,
                "file": path.name,
                "heat_duration_group_s": duration,
                "combo": combo,
                "repeat": repeat,
                "rows": len(data),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        data["Experiment_ID"] = run_id
        data["Experiment_Type"] = f"heat_{duration}s"
        data["run"] = run_id
        frames.append(data)

    manifest = pd.DataFrame(records).sort_values(["heat_duration_group_s", "combo", "repeat"])
    if manifest.heat_duration_group_s.value_counts().to_dict() != {4: 9, 6: 18}:
        raise ValueError("The source set does not contain exactly nine 4 s and eighteen 6 s runs.")

    # Each fan combination has a 6 s training run. Validation/test contain both
    # heat durations and are never used for fitting or augmentation.
    test = {"01_intake_weak_exhaust_weak copy", "05_intake_medium_exhaust_medium copy", "09_intake_strong_exhaust_strong copy",
            "01_intake_weak_exhaust_weak copy 2", "05_intake_medium_exhaust_medium copy 2", "09_intake_strong_exhaust_strong copy 2"}
    valid = {"02_intake_weak_exhaust_medium copy", "06_intake_medium_exhaust_strong copy", "08_intake_strong_exhaust_medium copy",
             "02_intake_weak_exhaust_medium copy 2", "06_intake_medium_exhaust_strong copy 2"}
    all_runs = set(manifest.run)
    if not test.issubset(all_runs) or not valid.issubset(all_runs):
        raise ValueError("Expected 4 s/6 s filenames are missing; do not silently change the split.")
    manifest["split"] = np.select(
        [manifest.run.isin(test), manifest.run.isin(valid)], ["test", "validation"], default="train"
    )
    return pd.concat(frames, ignore_index=True), manifest


def augment_lstm_examples(examples: list[dict], copies: int, sigma_c: float, seed: int) -> list[dict]:
    """Apply tiny measurement noise to train inputs only; labels and PWM stay real."""
    rng = np.random.default_rng(seed)
    augmented = list(examples)
    for _ in range(copies):
        for example in examples:
            item = dict(example)
            sequence = example["sequence"].copy()
            sequence[:, 0] += rng.normal(0.0, sigma_c, size=len(sequence)).astype(np.float32)
            item["sequence"] = sequence
            augmented.append(item)
    return augmented


def prediction(model: torch.nn.Module, examples: list[dict], mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    values, _, _, _ = arrays(examples, mean, std)
    with torch.no_grad():
        delta = model(torch.from_numpy(values)).cpu().numpy()
    return np.array([item["temp"] for item in examples]) + delta


def fpr(frame: pd.DataFrame) -> float | None:
    normal = ~frame.actual_over_limit.to_numpy(bool)
    if not normal.any():
        return None
    return float((frame.predicted_over_limit.to_numpy(bool)[normal]).mean())


def choose_window(candidates: list[dict]) -> dict:
    eligible = [row for row in candidates if row["validation_fpr"] is not None and row["validation_fpr"] <= 0.10]
    pool = eligible or candidates
    return min(
        pool,
        key=lambda row: (
            row["metrics"].false_negative_count,
            row["metrics"].mae_c,
            row["validation_fpr"] if row["validation_fpr"] is not None else 1.0,
            row["metrics"].false_positive_count,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument(
        "--horizon", type=int, default=DEFAULT_HORIZON_SECONDS,
        help="학습할 예측 시간(초). 3초와 5초는 각각 새 출력 폴더에서 학습·평가합니다.",
    )
    parser.add_argument("--augmentation-copies", type=int, default=3)
    parser.add_argument("--noise-sigma-c", type=float, default=0.01)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing study: {args.output}")
    if args.epochs < 1 or args.horizon < 1 or args.augmentation_copies < 0 or args.noise_sigma_c < 0:
        raise ValueError("epochs, horizon, augmentation copies, and noise sigma must be valid.")

    np.random.seed(42)
    torch.manual_seed(42)
    data, manifest = source_runs(args.source)
    args.output.mkdir(parents=True)
    (args.output / "data").mkdir()
    (args.output / "models").mkdir()
    (args.output / "reports").mkdir()
    manifest.to_csv(args.output / "data/run_manifest.csv", index=False)
    data.to_csv(args.output / "data/heat4_heat6_only.csv", index=False)

    train_runs = set(manifest.loc[manifest.split == "train", "run"])
    valid_runs = set(manifest.loc[manifest.split == "validation", "run"])
    test_runs = set(manifest.loc[manifest.split == "test", "run"])
    physics = fit_physics_model(data, train_runs)
    joblib.dump(physics, args.output / "models/physics_mpc_model.pkl")

    candidates: list[dict] = []
    for sequence_length in SEQUENCE_LENGTHS:
        examples = build_examples(data, sequence_length, args.horizon)
        train = [item for item in examples if item["run"] in train_runs]
        valid = [item for item in examples if item["run"] in valid_runs]
        if not train or not valid:
            raise ValueError("A split has no usable sequences.")
        augmented_train = augment_lstm_examples(train, args.augmentation_copies, args.noise_sigma_c, 31415 + sequence_length)
        model, mean, std = train_lstm(augmented_train, valid, args.epochs)
        valid_prediction = prediction(model, valid, mean, std)
        metrics, frame = evaluate(f"window_{sequence_length}_validation", valid, valid_prediction, args.output / "reports")
        candidates.append({"sequence_length": sequence_length, "model": model, "mean": mean, "std": std, "metrics": metrics, "validation_fpr": fpr(frame), "train_examples": len(train), "augmented_train_examples": len(augmented_train)})

    selected = choose_window(candidates)
    selected_length = selected["sequence_length"]
    examples = build_examples(data, selected_length, args.horizon)
    train = [item for item in examples if item["run"] in train_runs]
    test = [item for item in examples if item["run"] in test_runs]
    physics_prediction = np.array([
        physics_forecast(
            physics, item["temp"], item["rate"], item["intake"], item["exhaust"], args.horizon
        )
        for item in test
    ])
    physics_metrics, physics_frame = evaluate("physics_mpc", test, physics_prediction, args.output / "reports")
    lstm_prediction = prediction(selected["model"], test, selected["mean"], selected["std"])
    lstm_metrics, lstm_frame = evaluate("lstm", test, lstm_prediction, args.output / "reports")
    plot(test, physics_prediction, lstm_prediction, args.output / "reports")

    artifact = {
        "model_version": MODEL_VERSION,
        "state_dict": selected["model"].state_dict(),
        "feature_mean": selected["mean"].tolist(),
        "feature_std": selected["std"].tolist(),
        "feature_names": ["temperature_c", "temperature_rate_c_per_s", "actual_intake_pwm", "actual_exhaust_pwm", "planned_intake_pwm", "planned_exhaust_pwm"],
        "context_feature_count": 4,
        "sequence_length": selected_length,
        "horizon_seconds": args.horizon,
        "target_temp_c": TARGET_TEMP_C,
    }
    torch.save(artifact, args.output / "models/lstm_temperature_model.pt")

    report = {
        "scope": "4 s and 6 s heat runs only; 30 s heat runs excluded",
        "source_run_counts": {str(key): int(value) for key, value in manifest.heat_duration_group_s.value_counts().sort_index().items()},
        "split_run_counts": manifest.groupby("split").size().to_dict(),
        "horizon_seconds": args.horizon,
        "augmentation": {
            "applies_to": "LSTM train examples only",
            "method": "zero-mean Gaussian noise on past temperature inputs",
            "copies_per_original": args.augmentation_copies,
            "noise_sigma_c": args.noise_sigma_c,
            "physics_model": "fitted to unaugmented real train transitions to avoid inventing thermal dynamics",
        },
        "window_selection": [{key: (asdict(value) if key == "metrics" else value) for key, value in candidate.items() if key not in {"model", "mean", "std"}} for candidate in candidates],
        "selected_sequence_length": selected_length,
        "test_metrics": {"physics_mpc": asdict(physics_metrics), "lstm": asdict(lstm_metrics)},
        "observed_control": {
            "physics_mpc": evaluate_observed_control("physics_mpc", physics_frame, args.output / "reports"),
            "lstm": evaluate_observed_control("lstm", lstm_frame, args.output / "reports"),
        },
        "limitations": [
            "The 6 s collection is classified by experimental set; Heat_Load logs range from roughly 6 to 8 seconds in some files.",
            "Augmentation adds sensor-level input noise only and does not create new thermal or PWM-transition behavior.",
            "The small run count makes the held-out test estimate uncertain. Repeated independent trials remain necessary.",
            "This forecast evaluation does not prove closed-loop energy reduction.",
        ],
    }
    (args.output / "reports/evaluation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
