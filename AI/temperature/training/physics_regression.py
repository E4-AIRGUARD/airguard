"""Nonnegative regression baseline used in the frozen v6 comparison."""
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
MODEL_VERSION=2
CONTEXT_FEATURES=("temperature_c","temperature_rate_c_per_s","actual_intake_pwm","actual_exhaust_pwm")

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

