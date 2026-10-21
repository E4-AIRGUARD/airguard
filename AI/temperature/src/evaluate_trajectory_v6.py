"""Evaluate a frozen trajectory model and compare recorded-input control replays."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from project_paths import TRAINING_CSV, SPLIT_CSV
from train_lstm_v6 import build_items, predict, metrics
from trajectory_lstm_v6 import TrajectoryLSTM


def event_results(trace, data, label):
    rows = []
    for run, group in trace.groupby("run", sort=False):
        raw = data[data.run == run].sort_values("timestamp")
        heated = raw[raw.Heat_Load.fillna(0) > 0]
        up = (group.command_intake_pwm.diff() > 0) | (group.command_exhaust_pwm.diff() > 0)
        row = dict(model=label, run=run, heated=bool(len(heated)),
                   pwm_changes=int(group.pwm_changed.sum()), fan_up_count=int(up.sum()))
        if len(heated):
            start = float(heated.timestamp.iloc[0])
            crossing = raw[(raw.timestamp >= start) & (raw.Temp >= 26)].timestamp
            actions = group[(group.timestamp >= start) & up].timestamp
            before = raw[(raw.timestamp < start) & (raw.timestamp >= start - 5)].Temp.median()
            risen = (raw.Temp >= before + .15) & (raw.Temp.shift(-1) >= before + .15)
            onset = raw[(raw.timestamp >= start) & risen].timestamp
            action = float(actions.iloc[0]) if len(actions) else np.nan
            cross = float(crossing.iloc[0]) if len(crossing) else np.nan
            first_rise = float(onset.iloc[0]) if len(onset) else np.nan
            action_rows = group[group.timestamp == action]
            row.update(heat_start=start, first_fan_up=action, cross26=cross,
                       lead_s=cross-action, delay_after_rise_s=action-first_rise,
                       early=bool(np.isfinite(cross) and action < cross),
                       first_action_source=action_rows.prediction_source.iloc[0] if len(action_rows) else "",
                       first_action_reason=action_rows.reason.iloc[0] if len(action_rows) else "")
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    data = pd.read_csv(TRAINING_CSV)
    data["timestamp"] = pd.to_datetime(data.Time, utc=True, format="mixed").map(lambda t: t.timestamp())
    manifest = pd.read_csv(SPLIT_CSV)
    test_runs = set(manifest.loc[manifest.split == "test", "run"])
    items = [i for i in build_items(data) if i["run"] in test_runs]
    artifact = torch.load(args.model, map_location="cpu", weights_only=False)
    mean, std = np.array(artifact["feature_mean"]), np.array(artifact["feature_std"])
    model = TrajectoryLSTM(mean, std)
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    prediction, rates, scores = predict(model, items, mean, std)
    report = metrics(items, prediction, scores)
    actual = np.array([i["trajectory_target"] for i in items])
    labels = np.array([i["peak_label"] for i in items]) > 0
    alarms = scores >= artifact["risk_threshold"]
    report.update(peak_risk_fp=int((alarms & ~labels).sum()),
                  peak_risk_fn=int((~alarms & labels).sum()),
                  tail_rate_mae=float(np.abs(rates-(actual[:, 2]-actual[:, 1])/2).mean()))
    exported = []
    for item, predicted, rate, score in zip(items, prediction, rates, scores):
        row = dict(run=item["run"], timestamp=item["timestamp"],
                   current_temperature_c=item["temp"], predicted_tail_rate=float(rate), risk_score=float(score))
        for j, horizon in enumerate((1, 3, 5)):
            row[f"actual_{horizon}s"] = item["trajectory_target"][j]
            row[f"predicted_{horizon}s"] = float(predicted[j])
        exported.append(row)
    predictions = pd.DataFrame(exported)
    predictions.to_csv(args.output / "test_multihorizon_predictions.csv", index=False)
    predictions["error_5s"] = predictions.predicted_5s - predictions.actual_5s
    predictions["false_positive"] = (predictions.predicted_5s >= 26) & (predictions.actual_5s < 26)
    predictions["false_negative"] = (predictions.predicted_5s < 26) & (predictions.actual_5s >= 26)
    predictions[predictions.false_positive | predictions.false_negative].to_csv(
        args.output / "test_error_cases.csv", index=False)
    traces = {"v4 + v5 policy + assist": pd.read_csv(args.baseline / "prediction_and_pwm_trace.csv"),
              "v6 + trajectory policy + assist": pd.read_csv(args.replay / "prediction_and_pwm_trace.csv")}
    events = pd.concat([event_results(trace, data, label) for label, trace in traces.items()], ignore_index=True)
    events.to_csv(args.output / "event_comparison.csv", index=False)
    summary = []
    for label, folder in zip(traces, (args.baseline, args.replay)):
        result = json.loads((folder / "summary.json").read_text())
        e = events[events.model == label]
        crossed = e[e.cross26.notna()]
        normal = e[~e.heated]
        summary.append(dict(model=label, mae_c=result["mae_c"], rmse_c=result["rmse_c"],
                            fp=result["false_positive"], fn=result["false_negative"],
                            pwm_changes=int(e.pwm_changes.sum()), normal_fan_ups=int(normal.fan_up_count.sum()),
                            early_success=int(crossed.early.sum()), crossing_events=len(crossed),
                            mean_lead_s=float(crossed.lead_s.mean()),
                            mean_delay_after_rise_s=float(e.delay_after_rise_s.mean())))
    pd.DataFrame(summary).to_csv(args.output / "model_and_control_comparison.csv", index=False)
    (args.output / "test_metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    heated_runs = events.loc[events.heated, "run"].drop_duplicates().tolist()
    fig, axes = plt.subplots(len(heated_runs), 2, figsize=(17, 19))
    for row, run in enumerate(heated_runs):
        raw = data[data.run == run].sort_values("timestamp")
        start = float(raw.loc[raw.Heat_Load > 0, "timestamp"].iloc[0])
        ax, pwm = axes[row]
        ax.plot(raw.timestamp-start, raw.Temp, color="#172e42", label="Recorded actual")
        ax.axhline(26, color="#cf4949", linestyle="--", label="26 C upper limit")
        for label, color, style in ((list(traces)[0], "#999999", "--"), (list(traces)[1], "#7952b3", "-")):
            trace = traces[label][traces[label].run == run]
            ax.plot(trace.timestamp+5-start, trace.observed_pwm_prediction_c, color=color, linestyle=style, label=label)
            pwm.step(trace.timestamp-start, trace.command_exhaust_pwm, where="post", color=color, linestyle=style, label=label+" exhaust")
            pwm.step(trace.timestamp-start, trace.command_intake_pwm, where="post", color=color, linestyle=":", alpha=.7, label=label+" intake")
            changes = trace[trace.pwm_changed]
            pwm.scatter(changes.timestamp-start, changes.command_exhaust_pwm, color=color, s=14)
        ax.set_title(str(run), fontsize=8)
        ax.set_ylabel("Temperature (C)"); pwm.set_ylabel("Shadow PWM (%)")
        for panel in (ax, pwm):
            panel.set_xlim(-5, min(75, float(raw.timestamp.iloc[-1])-start)); panel.grid(alpha=.2)
            panel.set_xlabel("Seconds after recorded heat start")
        pwm.set_ylim(25, 105)
        if row == 0:
            ax.legend(fontsize=7); pwm.legend(fontsize=7)
    fig.suptitle("Frozen test: predictions aligned to target time / controller command changes\nRecorded temperatures are NOT affected by replayed commands", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, .96))
    fig.savefig(args.output / "prediction_and_pwm_comparison.png", dpi=150)
    fig.savefig(args.output / "prediction_and_pwm_comparison.pdf")
    plt.close(fig)
    print(json.dumps(dict(prediction=report, systems=summary), indent=2))


if __name__ == "__main__":
    main()
