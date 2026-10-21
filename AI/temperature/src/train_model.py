"""Heat_Load 없이 온도 예측기와 관측 PWM 결과를 학습·평가합니다."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, mean_absolute_error, precision_recall_fscore_support, r2_score
import torch
import torch.nn as nn

TARGET_TEMP_C = 26.0
MODEL_VERSION = 2
CONTEXT_FEATURES = ("temperature_c", "temperature_rate_c_per_s", "actual_intake_pwm", "actual_exhaust_pwm")
FEATURE_NAMES = (*CONTEXT_FEATURES, "planned_intake_pwm", "planned_exhaust_pwm")


@dataclass
class Metrics:
    mae_c: float
    rmse_c: float
    r2: float
    average_precision: float | None
    precision: float
    recall: float
    f1: float
    false_positive_count: int
    false_negative_count: int
    actual_alarm_count: int
    actual_normal_count: int
    sample_count: int


def load_data(path: str | Path) -> pd.DataFrame:
    data = pd.read_csv(path, parse_dates=["Time"])
    required = {"Time", "Temp", "Intake_PWM", "Exhaust_PWM"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"CSV 필수 열이 없습니다: {sorted(missing)}")
    if "Experiment_ID" in data and data["Experiment_ID"].notna().all():
        data["run"] = data["Experiment_ID"].astype(str)
    else:
        data = data.sort_values("Time").reset_index(drop=True)
        data["run"] = ((data.Intake_PWM != data.Intake_PWM.shift()) | (data.Exhaust_PWM != data.Exhaust_PWM.shift()) | (data.Time.diff().dt.total_seconds().fillna(1) > 2.5)).cumsum().astype(str)
    return data.sort_values(["run", "Time"]).reset_index(drop=True)


def split_runs(data: pd.DataFrame) -> tuple[set[str], set[str], set[str]]:
    if data.run.nunique() < 5:
        raise ValueError("최소 5개의 독립 실험 run이 필요합니다.")
    groups = [data] if "Experiment_Type" not in data else [group for _, group in data.groupby("Experiment_Type", sort=False)]
    train, valid, test = set(), set(), set()
    for group in groups:
        runs = list(group.groupby("run").Time.min().sort_values().index)
        if len(runs) < 3:
            train.update(runs)
            continue
        first = max(1, int(len(runs) * 0.6))
        second = min(max(first + 1, int(len(runs) * 0.8)), len(runs) - 1)
        train.update(runs[:first])
        valid.update(runs[first:second])
        test.update(runs[second:])
    return train, valid, test


def rates(run: pd.DataFrame) -> np.ndarray:
    t = (run.Time - run.Time.iloc[0]).dt.total_seconds().to_numpy(float)
    temp = run.Temp.to_numpy(float)
    output = np.zeros(len(run), dtype=np.float32)
    if len(run) > 1:
        dt = np.diff(t)
        delta = np.diff(temp)
        good = dt > 0.05
        output[1:][good] = delta[good] / dt[good]
    return np.clip(output, -2, 2)


def build_examples(data: pd.DataFrame, sequence_length: int, horizon_s: int) -> list[dict]:
    """과거 상태와 예측 구간에 실제로 유지된 PWM 계획을 한 사례로 만듭니다."""
    examples = []
    for run_id, run in data.groupby("run", sort=False):
        run = run.sort_values("Time").reset_index(drop=True)
        elapsed = (run.Time - run.Time.iloc[0]).dt.total_seconds().to_numpy(float)
        slope = rates(run)
        for index in range(sequence_length - 1, len(run)):
            future = int(np.searchsorted(elapsed, elapsed[index] + horizon_s, side="left"))
            if future >= len(run):
                continue
            intake, exhaust = float(run.Intake_PWM[index]), float(run.Exhaust_PWM[index])
            future_segment = run.iloc[index:future + 1]
            if not (np.allclose(future_segment.Intake_PWM, intake) and np.allclose(future_segment.Exhaust_PWM, exhaust)):
                continue
            start = index - sequence_length + 1
            history = run.iloc[start : index + 1]
            context = np.column_stack((history.Temp.to_numpy(np.float32), slope[start:index + 1], history.Intake_PWM.to_numpy(np.float32), history.Exhaust_PWM.to_numpy(np.float32)))
            plan = np.tile((intake, exhaust), (sequence_length, 1))
            changed = index > 0 and (intake != float(run.Intake_PWM[index - 1]) or exhaust != float(run.Exhaust_PWM[index - 1]))
            examples.append({"run": str(run_id), "timestamp": run.Time[index].isoformat(), "temp": float(run.Temp[index]), "rate": float(slope[index]), "intake": intake, "exhaust": exhaust, "target": float(run.Temp[future]), "transition": changed, "sequence": np.column_stack((context, plan)).astype(np.float32)})
    if not examples:
        raise ValueError("시퀀스를 만들 수 없습니다. run 길이·horizon·PWM 유지 시간을 확인하세요.")
    return examples


def fit_physics_model(data: pd.DataFrame, train_runs: set[str]) -> dict:
    train = data[data.run.isin(train_runs)]
    ambient = float(train.Temp.quantile(0.05))
    x, y = [], []
    for _, run in train.groupby("run", sort=False):
        run = run.sort_values("Time").reset_index(drop=True)
        elapsed = (run.Time - run.Time.iloc[0]).dt.total_seconds().to_numpy(float)
        slope = rates(run)
        for index in range(len(run) - 1):
            dt = elapsed[index + 1] - elapsed[index]
            if not 0.2 < dt <= 2.5:
                continue
            temp = float(run.Temp[index])
            gap = max(temp - ambient, 0)
            x.append([ambient - temp, slope[index], -(float(run.Intake_PWM[index]) / 100 * gap), -(float(run.Exhaust_PWM[index]) / 100 * gap)])
            y.append((float(run.Temp[index + 1]) - temp) / dt)
    if not y:
        raise ValueError("run 내부의 연속 데이터가 없습니다.")
    reg = LinearRegression(positive=True).fit(np.asarray(x), np.asarray(y))
    return {"model_version": MODEL_VERSION, "ambient_temp_c": ambient, "intercept": float(reg.intercept_), "env_gain": float(reg.coef_[0]), "trend_gain": float(reg.coef_[1]), "intake_fan_gain": float(reg.coef_[2]), "exhaust_fan_gain": float(reg.coef_[3]), "input_features": list(CONTEXT_FEATURES)}


def physics_forecast(model: dict, temp: float, slope: float, intake: float, exhaust: float, seconds: int) -> float:
    value, rate = temp, slope
    for _ in range(seconds):
        gap = max(value - model["ambient_temp_c"], 0)
        cooling = model["intake_fan_gain"] * intake / 100 * gap + model["exhaust_fan_gain"] * exhaust / 100 * gap
        rate = float(np.clip(model["intercept"] + model["env_gain"] * (model["ambient_temp_c"] - value) + model["trend_gain"] * rate - cooling, -2, 2))
        value += rate
    return value


class TemperatureLSTM(nn.Module):
    def __init__(self, feature_count: int = len(FEATURE_NAMES), hidden_size: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(feature_count, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(inputs)
        return self.head(output[:, -1, :]).squeeze(1)


def arrays(examples: list[dict], mean=None, std=None):
    x = np.stack([item["sequence"] for item in examples])
    y = np.array([item["target"] - item["temp"] for item in examples], dtype=np.float32)
    if mean is None:
        mean, std = x.mean((0, 1)), x.std((0, 1))
        std = np.where(std < 1e-6, 1.0, std)
    return ((x - mean) / std).astype(np.float32), y, mean, std


def train_lstm(train: list[dict], valid: list[dict], epochs: int):
    x, y, mean, std = arrays(train)
    valid_x_array, valid_y_array, _, _ = arrays(valid, mean, std)
    model = TemperatureLSTM(x.shape[2])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
    loss_fn = nn.MSELoss()
    best_state, best_loss, patience = None, float("inf"), 0
    train_x, train_y = torch.from_numpy(x), torch.from_numpy(y)
    valid_x = torch.from_numpy(valid_x_array)
    valid_y = torch.from_numpy(valid_y_array)
    for _ in range(epochs):
        model.train()
        order = torch.randperm(len(train_x))
        for start in range(0, len(order), 128):
            batch = order[start : start + 128]
            optimizer.zero_grad()
            loss = loss_fn(model(train_x[batch]), train_y[batch])
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            current_loss = loss_fn(model(valid_x), valid_y).item()
        if current_loss < best_loss:
            best_loss = current_loss
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= 10:
                break
    model.load_state_dict(best_state)
    model.eval()
    return model, mean, std


def frame_for(examples: list[dict], prediction: np.ndarray) -> pd.DataFrame:
    actual = np.array([item["target"] for item in examples])
    predicted_alarm = prediction >= TARGET_TEMP_C
    actual_alarm = actual >= TARGET_TEMP_C
    return pd.DataFrame(
        {
            "timestamp": [item["timestamp"] for item in examples],
            "run": [item["run"] for item in examples],
            "current_temp_c": [item["temp"] for item in examples],
            "current_rate_c_per_s": [item["rate"] for item in examples],
            "planned_intake_pwm": [item["intake"] for item in examples],
            "planned_exhaust_pwm": [item["exhaust"] for item in examples],
            "transition_at_start": [item["transition"] for item in examples],
            "actual_future_temp_c": actual,
            "predicted_future_temp_c": prediction,
            "error_c": prediction - actual,
            "actual_over_limit": actual_alarm,
            "predicted_over_limit": predicted_alarm,
        }
    )


def evaluate(name: str, examples: list[dict], prediction: np.ndarray, out: Path):
    frame = frame_for(examples, prediction)
    actual = frame.actual_over_limit.to_numpy()
    predicted = frame.predicted_over_limit.to_numpy()
    precision, recall, f1, _ = precision_recall_fscore_support(
        actual, predicted, average="binary", zero_division=0
    )
    frame["case"] = np.where(
        predicted & ~actual,
        "false_positive",
        np.where(~predicted & actual, "false_negative", ""),
    )
    frame.to_csv(out / f"{name}_test_predictions.csv", index=False)
    errors = (
        frame[frame.case != ""]
        .assign(absolute_error_c=lambda data: data.error_c.abs())
        .sort_values("absolute_error_c", ascending=False)
    )
    errors.head(30).to_csv(out / f"{name}_error_cases.csv", index=False)
    target = frame.actual_future_temp_c.to_numpy()
    ap = average_precision_score(actual, prediction) if actual.any() and (~actual).any() else None
    metrics = Metrics(
        mae_c=float(mean_absolute_error(target, prediction)),
        rmse_c=float(np.sqrt(np.mean((target - prediction) ** 2))),
        r2=float(r2_score(target, prediction)),
        average_precision=None if ap is None else float(ap),
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        false_positive_count=int((predicted & ~actual).sum()),
        false_negative_count=int((~predicted & actual).sum()),
        actual_alarm_count=int(actual.sum()),
        actual_normal_count=int((~actual).sum()),
        sample_count=len(frame),
    )
    return metrics, frame


def evaluate_observed_control(name: str, frame: pd.DataFrame, out: Path) -> dict:
    result = frame.copy()
    result["relative_fan_energy"] = (
        (result.planned_intake_pwm / 100) ** 3
        + (result.planned_exhaust_pwm / 100) ** 3
    )
    result["safety_decision_correct"] = (
        result.actual_over_limit == result.predicted_over_limit
    )
    result.to_csv(out / f"{name}_observed_control_evaluation.csv", index=False)
    return {"definition": "실제로 유지된 PWM의 10초 뒤 온도를 평가합니다.", "sample_count": len(result), "mean_relative_energy": float(result.relative_fan_energy.mean()), "safety_decision_accuracy": float(result.safety_decision_correct.mean()), "transition_examples": int(result.transition_at_start.sum()), "counterfactual_policy_verified": False, "limitation": "적용하지 않은 PWM 후보의 실제 결과는 없으므로 최저 에너지 후보 선택은 아직 검증되지 않았습니다."}


def plot(examples: list[dict], physics: np.ndarray, lstm: np.ndarray, out: Path):
    frame = pd.DataFrame(
        {
            "Time": pd.to_datetime([item["timestamp"] for item in examples], format="mixed"),
            "Experiment_ID": [item["run"] for item in examples],
            "Actual_Future_Temp": [item["target"] for item in examples],
            "Physics_MPC_Prediction": physics,
            "LSTM_Prediction": lstm,
        }
    )
    frame.to_csv(out / "test_predictions_comparison.csv", index=False)
    ids = frame.Experiment_ID.unique()
    fig, axes = plt.subplots(len(ids), 1, figsize=(12, 3 * len(ids)), squeeze=False)
    for axis, run_id in zip(axes[:, 0], ids):
        run = frame[frame.Experiment_ID == run_id]
        seconds = (run.Time - run.Time.iloc[0]).dt.total_seconds()
        axis.plot(seconds, run.Actual_Future_Temp, label="actual", color="black")
        axis.plot(seconds, run.Physics_MPC_Prediction, label="physics")
        axis.plot(seconds, run.LSTM_Prediction, label="LSTM")
        axis.axhline(TARGET_TEMP_C, color="gray", linestyle="--")
        axis.legend()
        axis.set_ylabel("°C")
    fig.tight_layout()
    fig.savefig(out / "test_prediction_comparison.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--models", default="models")
    parser.add_argument("--sequence-length", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=80)
    args = parser.parse_args()
    if args.sequence_length < 2:
        raise ValueError("--sequence-length는 2 이상이어야 합니다.")
    if args.horizon < 1 or args.epochs < 1:
        raise ValueError("--horizon과 --epochs는 1 이상이어야 합니다.")

    np.random.seed(42)
    torch.manual_seed(42)
    out = Path(args.models)
    out.mkdir(parents=True, exist_ok=True)
    data = load_data(args.csv)
    train_runs, valid_runs, test_runs = split_runs(data)
    all_examples = build_examples(data, args.sequence_length, args.horizon)
    train = [item for item in all_examples if item["run"] in train_runs]
    valid = [item for item in all_examples if item["run"] in valid_runs]
    test = [item for item in all_examples if item["run"] in test_runs]
    if not train or not valid or not test:
        raise ValueError("학습/검증/테스트 시퀀스가 부족합니다.")
    print(
        f"run 분할: train={len(train_runs)}, valid={len(valid_runs)}, test={len(test_runs)} | "
        f"PWM 전이: train={sum(item['transition'] for item in train)}, "
        f"test={sum(item['transition'] for item in test)}"
    )
    physics = fit_physics_model(data, train_runs)
    physics_prediction = np.array(
        [
            physics_forecast(
                physics,
                item["temp"],
                item["rate"],
                item["intake"],
                item["exhaust"],
                args.horizon,
            )
            for item in test
        ]
    )
    physics_metrics, physics_frame = evaluate(
        "physics_mpc", test, physics_prediction, out
    )
    joblib.dump(physics, out / "physics_mpc_model.pkl")

    lstm, mean, std = train_lstm(train, valid, args.epochs)
    test_x, _, _, _ = arrays(test, mean, std)
    with torch.no_grad():
        lstm_prediction = np.array([item["temp"] for item in test]) + lstm(
            torch.from_numpy(test_x)
        ).numpy()
    lstm_metrics, lstm_frame = evaluate("lstm", test, lstm_prediction, out)
    plot(test, physics_prediction, lstm_prediction, out)
    artifact = {
        "model_version": MODEL_VERSION,
        "state_dict": lstm.state_dict(),
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "feature_names": list(FEATURE_NAMES),
        "context_feature_count": len(CONTEXT_FEATURES),
        "sequence_length": args.sequence_length,
        "horizon_seconds": args.horizon,
        "target_temp_c": TARGET_TEMP_C,
    }
    torch.save(artifact, out / "lstm_temperature_model.pt")

    report = {
        "evaluation_definition": {
            "forecast_horizon_seconds": args.horizon,
            "temperature_limit_c": TARGET_TEMP_C,
            "input_features": list(FEATURE_NAMES),
            "prediction_evaluation": "10초 뒤 온도 및 26°C 오탐·미탐",
            "control_evaluation": "실제로 유지된 PWM 결과만 평가",
        },
        "data_limitations": [
            "Heat_Load는 메타데이터이며 모델 입력이 아닙니다.",
            "PWM 전이 사례가 없으면 후보 PWM 변경 효과는 검증되지 않습니다.",
            "반사실 후보의 최저 에너지 선택은 실험으로 검증해야 합니다.",
        ],
        "physics_mpc": asdict(physics_metrics),
        "lstm": asdict(lstm_metrics),
        "physics_mpc_observed_control": evaluate_observed_control(
            "physics_mpc", physics_frame, out
        ),
        "lstm_observed_control": evaluate_observed_control(
            "lstm", lstm_frame, out
        ),
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    (out / "evaluation_report.json").write_text(report_text)
    print(report_text)


if __name__ == "__main__":
    main()
