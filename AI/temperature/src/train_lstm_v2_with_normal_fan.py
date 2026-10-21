"""4·6초 가열 run과 정상 팬 운전 run으로 5초 예측 LSTM v2를 만듭니다."""

from __future__ import annotations

import argparse
import json
import unicodedata
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from train_heat4_heat6_runtime import augment_lstm_examples, prediction, source_runs
from train_model import (
    FEATURE_NAMES,
    MODEL_VERSION,
    TARGET_TEMP_C,
    arrays,
    build_examples,
    evaluate,
    train_lstm,
)


from project_paths import RAW_HEAT_DIR, NORMAL_SOURCE, RESULTS_DIR

DEFAULT_HEAT_SOURCE = RAW_HEAT_DIR
DEFAULT_NORMAL_SOURCE = NORMAL_SOURCE
DEFAULT_OUTPUT = RESULTS_DIR / 'lstm_v2'
HORIZON_SECONDS = 5
WINDOWS = (3, 4, 6, 8)

# 기존 4·6초 데이터와 같은 조합이 같은 split에 들어가도록 고정합니다.
TEST_COMBINATIONS = {(35, 35), (60, 60), (100, 100)}
VALIDATION_COMBINATIONS = {(35, 60), (60, 100), (100, 60)}


def split_for_pwm(intake: float, exhaust: float) -> str:
    pair = (int(round(intake)), int(round(exhaust)))
    if pair in TEST_COMBINATIONS:
        return "test"
    if pair in VALIDATION_COMBINATIONS:
        return "validation"
    return "train"


def load_heat_runs(source: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """30초 가열 데이터는 넣지 않고 현재 4·6초 run만 읽습니다."""
    data, manifest = source_runs(source)
    manifest = manifest.rename(columns={"file": "source", "heat_duration_group_s": "heat_duration_s"})
    manifest["source_group"] = "heat_" + manifest.heat_duration_s.astype(str) + "s"
    return data, manifest[["run", "source", "source_group", "rows", "split"]]


def load_normal_fan_runs(source: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """training_data_v2의 팬조합_정상운전(normal_fan) run만 보존해 추가합니다."""
    frame = pd.read_csv(source, parse_dates=["Time"])
    required = {"Time", "Experiment_ID", "Experiment_Type", "Temp", "Intake_PWM", "Exhaust_PWM"}
    if missing := required - set(frame.columns):
        raise ValueError(f"{source}: 필수 열 누락 {sorted(missing)}")
    normal_name = unicodedata.normalize("NFC", "팬조합_정상운전")
    name_match = frame.Experiment_ID.fillna("").map(
        lambda value: normal_name in unicodedata.normalize("NFC", str(value))
    )
    selected = frame[(frame.Experiment_Type == "normal_fan") | name_match].copy()
    if selected.empty:
        raise ValueError("training_data_v2.csv에서 팬조합_정상운전(normal_fan) 행을 찾지 못했습니다.")
    selected["run"] = "normal_fan::" + selected.Experiment_ID.astype(str)
    rows = []
    for run, part in selected.groupby("run", sort=True):
        rows.append({
            "run": run,
            "source": str(part.Source_File.iloc[0]) if "Source_File" in part else source.name,
            "source_group": "normal_fan",
            "rows": len(part),
            "split": split_for_pwm(part.Intake_PWM.iloc[0], part.Exhaust_PWM.iloc[0]),
        })
    return selected, pd.DataFrame(rows)


def false_positive_rate(frame: pd.DataFrame) -> float | None:
    normal = ~frame.actual_over_limit.to_numpy(bool)
    if not normal.any():
        return None
    return float(frame.predicted_over_limit.to_numpy(bool)[normal].mean())


def choose_window(candidates: list[dict]) -> dict:
    """미탐을 우선하고, 정상 팬 운전에서의 오탐과 MAE로 동점을 풉니다."""
    return min(
        candidates,
        key=lambda row: (
            row["metrics"].false_negative_count,
            row["fpr"] if row["fpr"] is not None else 1.0,
            row["metrics"].mae_c,
            row["metrics"].false_positive_count,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heat-source", type=Path, default=DEFAULT_HEAT_SOURCE)
    parser.add_argument("--normal-source", type=Path, default=DEFAULT_NORMAL_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--augmentation-copies", type=int, default=3)
    parser.add_argument("--noise-sigma-c", type=float, default=0.01)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"기존 연구 결과를 덮어쓰지 않습니다: {args.output}")
    if args.epochs < 1 or args.augmentation_copies < 0 or args.noise_sigma_c < 0:
        raise ValueError("epochs와 증강 설정값을 확인하세요.")

    np.random.seed(42)
    torch.manual_seed(42)
    heat, heat_manifest = load_heat_runs(args.heat_source)
    normal, normal_manifest = load_normal_fan_runs(args.normal_source)
    data = pd.concat([heat, normal], ignore_index=True, sort=False)
    # 두 수집 세트의 절대 시각 표기가 달라도 run 내부 경과시간은 유지합니다.
    data["Time"] = pd.to_datetime(data["Time"], errors="raise", format="mixed", utc=True).dt.tz_localize(None)
    manifest = pd.concat([heat_manifest, normal_manifest], ignore_index=True)
    train_runs = set(manifest.loc[manifest.split == "train", "run"])
    valid_runs = set(manifest.loc[manifest.split == "validation", "run"])
    test_runs = set(manifest.loc[manifest.split == "test", "run"])
    if not (train_runs and valid_runs and test_runs):
        raise ValueError("train/validation/test run 분할이 비어 있습니다.")

    args.output.mkdir(parents=True)
    (args.output / "data").mkdir()
    (args.output / "models").mkdir()
    (args.output / "reports").mkdir()
    manifest.to_csv(args.output / "data/run_manifest.csv", index=False)
    data.to_csv(args.output / "data/heat4_heat6_plus_normal_fan.csv", index=False)

    candidates: list[dict] = []
    for window in WINDOWS:
        examples = build_examples(data, window, HORIZON_SECONDS)
        train = [item for item in examples if item["run"] in train_runs]
        valid = [item for item in examples if item["run"] in valid_runs]
        if not train or not valid:
            raise ValueError(f"window={window}: train 또는 validation 예제가 없습니다.")
        augmented = augment_lstm_examples(
            train, args.augmentation_copies, args.noise_sigma_c, 22000 + window
        )
        model, mean, std = train_lstm(augmented, valid, args.epochs)
        valid_prediction = prediction(model, valid, mean, std)
        metrics, frame = evaluate(f"window_{window}_validation", valid, valid_prediction, args.output / "reports")
        candidates.append({
            "window": window,
            "model": model,
            "mean": mean,
            "std": std,
            "metrics": metrics,
            "fpr": false_positive_rate(frame),
            "train_examples": len(train),
            "augmented_train_examples": len(augmented),
        })

    selected = choose_window(candidates)
    examples = build_examples(data, selected["window"], HORIZON_SECONDS)
    test = [item for item in examples if item["run"] in test_runs]
    test_prediction = prediction(selected["model"], test, selected["mean"], selected["std"])
    test_metrics, _ = evaluate("lstm_v2", test, test_prediction, args.output / "reports")
    artifact = {
        "model_version": MODEL_VERSION,
        "state_dict": selected["model"].state_dict(),
        "feature_mean": selected["mean"].tolist(),
        "feature_std": selected["std"].tolist(),
        "feature_names": list(FEATURE_NAMES),
        "context_feature_count": 4,
        "sequence_length": selected["window"],
        "horizon_seconds": HORIZON_SECONDS,
        "target_temp_c": TARGET_TEMP_C,
        "training_scope": "4s/6s heating plus normal fan-operation runs",
    }
    torch.save(artifact, args.output / "models/lstm_temperature_model_v2.pt")
    report = {
        "horizon_seconds": HORIZON_SECONDS,
        "source_rows": {"heat_4s_6s": int(len(heat)), "normal_fan": int(len(normal))},
        "source_runs": {"heat_4s_6s": int(len(heat_manifest)), "normal_fan": int(len(normal_manifest))},
        "split_runs": manifest.groupby(["source_group", "split"]).size().unstack(fill_value=0).to_dict(),
        "augmentation": {
            "method": "zero-mean Gaussian noise on past temperature inputs only",
            "copies_per_train_example": args.augmentation_copies,
            "noise_sigma_c": args.noise_sigma_c,
        },
        "window_selection": [
            {
                "sequence_length": row["window"],
                "validation_fpr": row["fpr"],
                "train_examples": row["train_examples"],
                "augmented_train_examples": row["augmented_train_examples"],
                "metrics": asdict(row["metrics"]),
            }
            for row in candidates
        ],
        "selected_sequence_length": selected["window"],
        "test_metrics": asdict(test_metrics),
        "limitations": [
            "normal_fan data shows stable fan operation, but does not by itself prove cooling strength at every temperature.",
            "Fan combinations are assigned to one run split to prevent the same stable run from leaking into train and test.",
            "A separate, repeated closed-loop test is required before using v2 for live fan control.",
        ],
    }
    (args.output / "reports/evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
