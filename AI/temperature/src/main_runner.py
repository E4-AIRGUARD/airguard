"""AirGuard: 매초 온도를 출력하고 Physics MPC 또는 LSTM으로 PWM을 정합니다.

학습 후 예시:
  python3 src/main_runner.py --controller-model physics --dry-run
  python3 src/main_runner.py --controller-model lstm --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from project_paths import PROJECT_ROOT, MODELS_DIR
from control_policy_v5 import HysteresisPolicy, SafeEnergySwitchGate

import joblib
import numpy as np

from fan_control import EspFanController
from energy_switch import EnergySwitchGate
from pwm_stability import PwmStabilityGuard
from raw_rise_assist import RawRiseAssist
from sensor_buffer import SensorBuffer, SensorSample
from sensor_receiver import DummySensorPublisher, SerialConfig, SerialSensorReceiver

try:
    import torch
    import torch.nn as nn
except ImportError:
    torch = None
    nn = None


TARGET_TEMPERATURE_C = 26.0
# 팬의 기동·안정 운전 검증값입니다. 정상 운전 중에는 두 팬을 이 값 아래로
# 내리지 않습니다. (전원 차단은 별도 안전 절차가 필요합니다.)
MINIMUM_PWM = 35
IDLE_PWM = MINIMUM_PWM
SENSOR_TIMEOUT_S = 5.0
COMMAND_HEARTBEAT_S = 5.0
MAX_RECOVERY_SECONDS = 600
ECO_LIMIT_C = 25.0
PREVENTIVE_LIMIT_C = 25.5
# 아래 조합은 4초·6초 가열 실험에서 실제로 기록된 배기 우세 운전점입니다.
VALIDATED_PWM_COMBINATIONS = (
    (35, 35), (35, 60), (35, 100), (60, 60), (60, 100), (100, 100),
)


@dataclass(frozen=True)
class CandidateComparison:
    """대시보드에 보여 줄 대표 PWM 후보의 예측 결과입니다."""

    intake_pwm: int
    exhaust_pwm: int
    predicted_temp_c: float
    relative_energy: float
    safe: bool
    selected: bool = False
    recovery_seconds: int | None = None
    total_relative_energy: float | None = None
    control_cost: float | None = None
    predicted_rate_c_per_s: float | None = None
    upper_temp_c: float | None = None


@dataclass(frozen=True)
class InferenceResult:
    """한 번의 제어 판단 결과입니다."""

    predicted_temp_c: float
    intake_pwm: int
    exhaust_pwm: int
    recovery_seconds: int | None
    model_name: str
    candidate_comparison: tuple[CandidateComparison, ...] = ()
    policy_mode: str = "대기"
    policy_limit_c: float | None = None
    all_candidates: tuple[CandidateComparison, ...] = ()


@dataclass
class RuntimeState:
    latest_sample: SensorSample | None = None
    last_command_at: float = 0.0
    last_intake_percent: int = -1
    last_exhaust_percent: int = -1


@dataclass(frozen=True)
class TrialConfig:
    """실제 센서·팬 연결 시험에서만 함께 저장하는 메타데이터입니다."""

    trial_id: str
    note: str | None


def validated_pwm_candidates(min_pwm: int) -> list[tuple[int, int]]:
    """학습 데이터에 있는 배기 우세 PWM 조합만 후보로 사용합니다."""
    candidates = [
        (intake, exhaust)
        for intake, exhaust in VALIDATED_PWM_COMBINATIONS
        if intake >= min_pwm and exhaust >= min_pwm
    ]
    if not candidates:
        return [(100, 100)]
    return candidates


def candidates_for_stage(
    candidates: list[tuple[int, int]], stage: str
) -> list[tuple[int, int]]:
    """온도 위험도에 따라 검증된 후보의 운전 단계를 제한합니다."""
    if stage == "절전 유지":
        return candidates
    if stage == "예방 냉각":
        return [pair for pair in candidates if pair != (35, 35)]
    return [pair for pair in candidates if pair[1] == 100]


def relative_fan_energy(intake: int, exhaust: int) -> float:
    """실측 전력이 없을 때 쓰는 PWM³ 기반의 무차원 비교 점수입니다."""
    return (intake / 100.0) ** 3 + (exhaust / 100.0) ** 3


def normalize_lstm_window(controller, window: np.ndarray) -> np.ndarray:
    """모델 파일에 저장된 입력 방식으로 단일/후보 묶음 시계열을 정규화합니다."""
    values = np.asarray(window, dtype=np.float32).copy()
    if getattr(controller, 'relative_temperature_input', False):
        values[..., :, 0] -= values[..., -1:, 0]
    return (values-controller.mean)/controller.std


def candidate_summary(
    candidates: list[CandidateComparison], selected: CandidateComparison
) -> tuple[CandidateComparison, ...]:
    """선택 근거를 보이도록 최소 에너지 후보 일부만 남긴다."""
    safe = [candidate for candidate in candidates if candidate.safe]
    unsafe = [candidate for candidate in candidates if not candidate.safe]
    displayed = safe[:5]
    if unsafe:
        displayed.append(unsafe[0])
    if selected not in displayed:
        displayed.append(selected)
    return tuple(
        replace(candidate, selected=candidate == selected)
        for candidate in sorted(displayed, key=lambda candidate: float('inf') if candidate.total_relative_energy is None else candidate.total_relative_energy)
    )


def select_energy_plan(
    controller,
    history: np.ndarray,
    limit_c: float,
    allowed_candidates: list[tuple[int, int]] | None = None,
) -> InferenceResult:
    """10초 예측으로 안전성을 확인하고, 전력×회복시간으로 후보를 비교합니다."""
    plans = np.asarray(allowed_candidates or controller.pwm_candidates, dtype=float)
    count = len(plans)
    values = np.full(count, history[-1, 0], dtype=float)
    rates = np.full(count, history[-1, 1], dtype=float)
    contexts = np.repeat(history[None, :, :], count, axis=0)
    recovery = np.full(count, 0 if values[0] <= limit_c else -1, dtype=int)
    crossed = np.full(count, values[0] > limit_c, dtype=bool)
    predicted = values.copy()
    # LSTM은 학습된 예측 간격으로만 추론합니다.
    step = controller.horizon_seconds if isinstance(controller, LstmMpcController) else 1
    for start in range(0, MAX_RECOVERY_SECONDS - step + 1, step):
        # 회복시간 추정 동안에는 각 후보 PWM을 일정하게 유지한다고 가정합니다.
        active = plans
        if isinstance(controller, PhysicsMpcController):
            ambient = controller.model['ambient_temp_c']
            gap = np.maximum(values - ambient, 0.0)
            if 'intake_fan_gain' in controller.model:
                cooling = gap * (controller.model['intake_fan_gain'] * active[:, 0]
                                 + controller.model['exhaust_fan_gain'] * active[:, 1]) / 100
            else:
                cooling = gap * controller.model['fan_gain'] * active.mean(axis=1) / 100
            rates = (controller.model['intercept'] + controller.model['env_gain'] * (ambient - values)
                     + controller.model.get('trend_gain', 0.0) * np.clip(rates, -2, 2) - cooling)
            next_values = values + rates
        else:
            # 첫 예측은 전체 후보, 이후는 안전 조건을 통과한 미회복 후보만 추론합니다.
            indices = np.arange(count) if start == 0 else np.flatnonzero(
                (recovery < 0) & ((predicted <= limit_c) | np.all(plans == 100, axis=1)))
            if not len(indices):
                break
            subset = contexts[indices]
            window = np.concatenate((subset, np.repeat(active[indices, None, :], subset.shape[1], axis=1)), axis=2)
            normalized = normalize_lstm_window(controller, window).astype(np.float32)
            with torch.inference_mode():
                # 일부 JetPack PyTorch는 NumPy 연동 없이 빌드됩니다.
                inputs = torch.tensor(normalized.tolist(), dtype=torch.float32)
                next_values = values.copy()
                next_values[indices] += np.asarray(controller.model(inputs).tolist(), dtype=float)
            rates = (next_values - values) / step
            # 다음 입력도 원래의 1초 간격을 유지합니다. 중간값은 선형 보간입니다.
            for offset in range(1, step + 1):
                row = subset[:, -1, :].copy()
                row[:, 0] = values[indices] + (next_values[indices] - values[indices]) * offset / step
                row[:, 1] = rates[indices]
                row[:, 2:4] = active[indices]
                if subset.shape[2] == 7:
                    # 회복시간 재생의 미래 변화율은 예측 온도에서 근사합니다.
                    for column, lag in enumerate((1,3,5), 4):
                        row[:, column] = (row[:, 0]-subset[:, -lag, 0])/lag if len(subset[0]) >= lag else 0.
                subset = np.concatenate((subset[:, 1:, :], row[:, None, :]), axis=1)
            contexts[indices] = subset
        elapsed = start + step
        if elapsed == controller.horizon_seconds:
            predicted = next_values.copy()
        above = next_values > limit_c
        recovery[above & ~crossed] = -1
        crossed |= above
        recovery[(recovery < 0) & crossed & ~above & np.isfinite(next_values)] = elapsed
        values = next_values
        if elapsed >= controller.horizon_seconds and np.all(recovery >= 0):
            break
    candidates = []
    for index, (intake, exhaust) in enumerate(plans.astype(int)):
        intake, exhaust = int(intake), int(exhaust)
        power = relative_fan_energy(intake, exhaust)
        duration = int(recovery[index]) if recovery[index] >= 0 else None
        cost = power * duration if duration is not None else None
        if getattr(controller,'control_policy_version','v4')=='v5':
            safe=bool(np.isfinite(predicted[index]) and predicted[index]<controller.target_c)
        else:
            safe = bool(np.isfinite(predicted[index]) and predicted[index] <= limit_c and duration is not None)
        candidates.append(CandidateComparison(
            intake, exhaust, float(predicted[index]), relative_fan_energy(intake, exhaust), safe,
            recovery_seconds=duration,
            total_relative_energy=cost,
        ))
    # 이미 정상인 후보는 시간·비용이 0이므로 상대 전력이 낮은 후보를 택합니다.
    candidates.sort(key=lambda item: (float('inf') if item.total_relative_energy is None else item.total_relative_energy, item.relative_energy))
    feasible = [item for item in candidates if item.safe]
    v5=getattr(controller,'control_policy_version','v4')=='v5'
    current_pair=getattr(controller,'current_command_pair',None)
    current_candidate=next((c for c in candidates if (c.intake_pwm,c.exhaust_pwm)==current_pair),None)
    if feasible:
        # Preserve a safe existing command rather than invent an unknown recovery cost.
        selected=(current_candidate if v5 and current_candidate is not None and current_candidate.safe
                  and current_candidate.recovery_seconds is None else feasible[0])
    elif v5:
        finite_candidates=[c for c in candidates if np.isfinite(c.predicted_temp_c)]
        selected=min(finite_candidates,key=lambda c:(c.predicted_temp_c,c.relative_energy)) if finite_candidates else (current_candidate or candidates[0])
    else:
        selected=next((item for item in candidates if item.intake_pwm==100 and item.exhaust_pwm==100),candidates[-1])
    name = getattr(controller, "display_name", "Physics" if isinstance(controller, PhysicsMpcController) else "LSTM")
    return InferenceResult(selected.predicted_temp_c, selected.intake_pwm, selected.exhaust_pwm,
                           selected.recovery_seconds, f'{name} MPC · 전력×회복시간' if feasible else (f'{name} MPC 예측 불확실 후보 비교' if v5 else f'{name} MPC 안전 냉각'),
                           candidate_summary(candidates, selected), all_candidates=tuple(candidates))


class TemperatureLSTM(nn.Module if nn is not None else object):
    """train_model.py와 같은 구조여야 저장된 가중치를 읽을 수 있습니다."""

    def __init__(self, feature_count: int = 6, hidden_size: int = 32):
        if nn is None:
            raise RuntimeError("LSTM 실행에는 JetPack에 맞는 PyTorch가 필요합니다.")
        super().__init__()
        self.lstm = nn.LSTM(feature_count, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, inputs):
        output, _ = self.lstm(inputs)
        return self.head(output[:, -1, :]).squeeze(1)


class MultiHorizonTemperatureLSTM(nn.Module if nn is not None else object):
    """3·5·10초처럼 여러 미래 시점의 변화량을 한 번에 출력합니다."""

    def __init__(self, feature_count: int, horizon_count: int, hidden_size: int = 32):
        if nn is None:
            raise RuntimeError("LSTM 실행에는 JetPack에 맞는 PyTorch가 필요합니다.")
        super().__init__()
        self.lstm = nn.LSTM(feature_count, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, horizon_count)

    def forward(self, inputs):
        output, _ = self.lstm(inputs)
        return self.head(output[:, -1, :])


class PhysicsMpcController:
    """배기 우세 1% PWM 후보에서 최소 상대 에너지 후보를 선택합니다."""

    def __init__(self, model_path: Path, target_c: float, horizon_seconds: int, min_pwm: int):
        self.model = joblib.load(model_path)
        self.target_c = target_c
        self.horizon_seconds = horizon_seconds
        self.pwm_candidates = validated_pwm_candidates(min_pwm)

    def forecast(self, temperature: float, rate: float, intake: float, exhaust: float, seconds: int) -> float:
        """Heat_Load 대신 관측된 최근 온도 변화율을 짧게 반영합니다."""
        value, trend = float(temperature), float(rate)
        ambient = self.model["ambient_temp_c"]
        for _ in range(seconds):
            temperature_gap = max(value - ambient, 0.0)
            intake_effect = (intake / 100.0) * temperature_gap
            exhaust_effect = (exhaust / 100.0) * temperature_gap
            if "intake_fan_gain" in self.model:
                cooling = (
                    self.model["intake_fan_gain"] * intake_effect
                    + self.model["exhaust_fan_gain"] * exhaust_effect
                )
            else:
                # 기존 모델 파일도 재학습 전까지는 실행할 수 있게 유지합니다.
                cooling = self.model["fan_gain"] * ((intake_effect + exhaust_effect) / 2.0)
            trend = (
                self.model["intercept"]
                + self.model["env_gain"] * (ambient - value)
                + self.model.get("trend_gain", 0.0) * np.clip(trend, -2.0, 2.0)
                - cooling
            )
            value += trend
        return value

    def forecast_current(self, history: np.ndarray) -> float:
        """현재 적용 중인 PWM을 그대로 유지했을 때의 예측 온도입니다."""
        return self.forecast(
            float(history[-1, 0]), float(history[-1, 1]),
            float(history[-1, 2]), float(history[-1, 3]), self.horizon_seconds,
        )

    def choose(
        self,
        history: np.ndarray,
        limit_c: float | None = None,
        allowed_candidates: list[tuple[int, int]] | None = None,
    ) -> InferenceResult:
        return select_energy_plan(
            self, history, self.target_c if limit_c is None else limit_c, allowed_candidates
        )

class LstmMpcController:
    """LSTM의 온도 예측을 MPC 후보 탐색에 사용합니다."""

    def __init__(self, model_path: Path, target_c: float, min_pwm: int):
        if torch is None:
            raise RuntimeError("LSTM 실행에는 JetPack에 맞는 PyTorch가 필요합니다.")
        try:
            artifact = torch.load(model_path, map_location="cpu", weights_only=False)
        except TypeError:  # 구형 JetPack PyTorch 호환
            artifact = torch.load(model_path, map_location="cpu")
        if artifact.get("model_version") not in (2, 4, 5, 6) or artifact.get("feature_names") is None:
            raise RuntimeError("LSTM 모델 형식이 오래되었습니다. 새 train_model.py로 모델을 다시 학습하세요.")
        self.relative_temperature_input = artifact.get('architecture') in ('stable_relative_lstm','multirate_relative_lstm','trajectory_relative_lstm')
        if artifact.get('model_version') == 4 and not self.relative_temperature_input:
            raise ValueError('지원하지 않는 LSTM v4 구조입니다.')
        if artifact.get('architecture')=='trajectory_relative_lstm':
            if artifact.get('model_version')!=6:raise ValueError('궤적 모델 형식 오류')
            from trajectory_lstm_v6 import TrajectoryLSTM
            self.model=TrajectoryLSTM(artifact['feature_mean'],artifact['feature_std'])
        elif artifact.get('model_version')==6:
            raise ValueError('지원하지 않는 궤적 모델 구조')
        elif artifact.get('architecture') == 'multirate_relative_lstm':
            if artifact.get('model_version') != 5:
                raise ValueError('다중 변화율 LSTM 형식이 맞지 않습니다.')
            from multirate_temperature_lstm import MultiRateTemperatureLSTM
            self.model = MultiRateTemperatureLSTM(artifact['feature_mean'],artifact['feature_std'],int(artifact['horizon_seconds']))
        elif artifact.get('model_version') == 5:
            raise ValueError('지원하지 않는 LSTM v5 구조입니다.')
        elif self.relative_temperature_input:
            from stable_temperature_lstm import StableTemperatureLSTM
            self.model = StableTemperatureLSTM(
                artifact['feature_mean'], artifact['feature_std'],
                horizon=int(artifact['horizon_seconds']), gated=artifact['gated'],
            )
        else:
            self.model = TemperatureLSTM(feature_count=len(artifact["feature_names"]))
        self.model.load_state_dict(artifact["state_dict"])
        self.model.eval()
        self.mean = np.asarray(artifact["feature_mean"], dtype=np.float32)
        self.std = np.asarray(artifact["feature_std"], dtype=np.float32)
        self.sequence_length = int(artifact["sequence_length"])
        self.context_feature_count = int(artifact["context_feature_count"])
        self.horizon_seconds = int(artifact["horizon_seconds"])
        self.model_path = str(model_path.resolve())
        self.model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        self.default_control_policy=artifact.get('control_policy','v4')
        self.recommended_raw_rise_assist=artifact.get('recommended_raw_rise_assist',False)
        self.prediction_margin=np.array(artifact.get('prediction_margin_c',[0.,0.,0.]))
        self.risk_threshold=artifact.get('risk_threshold',1.)
        self.rate_weight=artifact.get('rate_weight',25.)
        self.upper_risk_count=0
        self.display_name = f"LSTM {model_path.stem} ({self.horizon_seconds}s)"
        self.target_c = target_c
        self.pwm_candidates = validated_pwm_candidates(min_pwm)

    def forecast(self, history: np.ndarray, intake: int, exhaust: int) -> float:
        context = history[-self.sequence_length :].copy()
        if context.shape[1] != self.context_feature_count:
            raise ValueError("센서 버퍼와 LSTM 모델의 입력 형식이 다릅니다.")
        window = np.column_stack((context, np.tile((intake, exhaust), (len(context), 1))))
        normalized = normalize_lstm_window(self, window)
        with torch.inference_mode():
            inputs = torch.tensor(normalized.tolist(), dtype=torch.float32)[None, :, :]
            delta = self.model(inputs).item()
        return float(window[-1, 0] + delta)

    def forecast_current(self, history: np.ndarray) -> float:
        return self.forecast(history, int(history[-1, 2]), int(history[-1, 3]))

    def trajectory(self,history,intake,exhaust):
        context=history[-self.sequence_length:].copy()
        window=np.column_stack((context,np.tile((intake,exhaust),(len(context),1))))
        normalized=normalize_lstm_window(self,window)
        with torch.inference_mode():
            delta,rate,logit=self.model.details(torch.tensor(normalized.tolist(),dtype=torch.float32)[None])
        temperatures=float(context[-1,0])+np.array(delta[0].tolist())
        rates=np.diff(np.r_[float(context[-1,0]),temperatures])/np.array([1.,2.,2.])
        return dict(temperatures=temperatures,rate=float(rate.item()),max_rate=float(np.max(rates)),
                    risk_score=float(torch.sigmoid(logit).item()))

    def upper_risk(self,history,temperature):
        detail=self.trajectory(history,int(history[-1,2]),int(history[-1,3]))
        risky=np.max(detail['temperatures']+self.prediction_margin)>=self.target_c or (
            detail['risk_score']>=self.risk_threshold and history[-1,4]>.03)
        self.upper_risk_count=self.upper_risk_count+1 if risky else 0
        return temperature>=self.target_c or (risky and history[-1,4]>=.08) or self.upper_risk_count>=2

    def choose(
        self,
        history: np.ndarray,
        limit_c: float | None = None,
        allowed_candidates: list[tuple[int, int]] | None = None,
    ) -> InferenceResult:
        if self.context_feature_count==8:
            from trajectory_control_v6 import choose_trajectory
            return choose_trajectory(self,history[-self.sequence_length:],allowed_candidates)
        return select_energy_plan(
            self,
            history[-self.sequence_length :],
            self.target_c if limit_c is None else limit_c,
            allowed_candidates,
        )


class MultiHorizonLstmMpcController:
    """한 LSTM의 다중 미래 예측 중 선택한 시간대를 MPC 판단에 사용합니다."""

    display_name = "Multi-horizon LSTM"

    def __init__(self, model_path: Path, target_c: float, min_pwm: int, selected_horizon: int):
        if torch is None:
            raise RuntimeError("LSTM 실행에는 JetPack에 맞는 PyTorch가 필요합니다.")
        try:
            artifact = torch.load(model_path, map_location="cpu", weights_only=False)
        except TypeError:
            artifact = torch.load(model_path, map_location="cpu")
        if artifact.get("model_version") != 3 or artifact.get("architecture") != "multi_horizon_lstm":
            raise RuntimeError("다중 예측 LSTM 형식이 아닙니다. train_multi_horizon_lstm.py로 다시 학습하세요.")
        self.horizons_seconds = tuple(int(value) for value in artifact["horizons_seconds"])
        if selected_horizon not in self.horizons_seconds:
            raise ValueError(f"--multi-lstm-horizon은 {self.horizons_seconds} 중 하나여야 합니다.")
        self.horizon_index = self.horizons_seconds.index(selected_horizon)
        self.horizon_seconds = selected_horizon
        self.model = MultiHorizonTemperatureLSTM(
            feature_count=len(artifact["feature_names"]), horizon_count=len(self.horizons_seconds)
        )
        self.model.load_state_dict(artifact["state_dict"])
        self.model.eval()
        self.mean = np.asarray(artifact["feature_mean"], dtype=np.float32)
        self.std = np.asarray(artifact["feature_std"], dtype=np.float32)
        self.sequence_length = int(artifact["sequence_length"])
        self.context_feature_count = int(artifact["context_feature_count"])
        self.target_c = target_c
        self.pwm_candidates = validated_pwm_candidates(min_pwm)

    def forecast(self, history: np.ndarray, intake: int, exhaust: int) -> float:
        context = history[-self.sequence_length :].copy()
        if context.shape[1] != self.context_feature_count:
            raise ValueError("센서 버퍼와 다중 예측 LSTM의 입력 형식이 다릅니다.")
        window = np.column_stack((context, np.tile((intake, exhaust), (len(context), 1))))
        normalized = (window - self.mean) / self.std
        with torch.inference_mode():
            inputs = torch.tensor(normalized.tolist(), dtype=torch.float32)[None, :, :]
            delta = self.model(inputs)[0, self.horizon_index].item()
        return float(window[-1, 0] + delta)

    def forecast_current(self, history: np.ndarray) -> float:
        return self.forecast(history, int(history[-1, 2]), int(history[-1, 3]))

    def choose(
        self,
        history: np.ndarray,
        limit_c: float | None = None,
        allowed_candidates: list[tuple[int, int]] | None = None,
    ) -> InferenceResult:
        return select_energy_plan(
            self,
            history[-self.sequence_length :],
            self.target_c if limit_c is None else limit_c,
            allowed_candidates,
        )

class EventStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS control_events_v2 "
                "(timestamp REAL, temperature_c REAL, predicted_temp_c REAL, "
                "intake_percent INTEGER, exhaust_percent INTEGER, recovery_seconds INTEGER, reason TEXT)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS sensor_readings "
                "(timestamp REAL PRIMARY KEY, temperature_c REAL NOT NULL, humidity_pct REAL, "
                "pm25_ug_m3 REAL, heat_load INTEGER, intake_rpm REAL, exhaust_rpm REAL, "
                "intake_pwm REAL, exhaust_pwm REAL, smoothed_temperature_c REAL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS control_status "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), timestamp REAL, temperature_c REAL, "
                "predicted_temp_c REAL, intake_percent INTEGER, exhaust_percent INTEGER, "
                "recovery_seconds INTEGER, model_name TEXT, reason TEXT, candidate_json TEXT, "
                "policy_mode TEXT, policy_limit_c REAL, last_command_at REAL, command_sent INTEGER)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS closed_loop_trial_samples "
                "(trial_id TEXT NOT NULL, timestamp REAL NOT NULL, temperature_c REAL NOT NULL, "
                "predicted_temp_c REAL, command_intake_pwm INTEGER, command_exhaust_pwm INTEGER, "
                "feedback_intake_pwm REAL, feedback_exhaust_pwm REAL, intake_rpm REAL, exhaust_rpm REAL, "
                "model_name TEXT, policy_mode TEXT, trial_note TEXT, reason TEXT, command_sent INTEGER NOT NULL, "
                "PRIMARY KEY (trial_id, timestamp))"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS closed_loop_trial_events "
                "(trial_id TEXT NOT NULL, timestamp REAL NOT NULL, event_type TEXT NOT NULL, note TEXT, "
                "PRIMARY KEY (trial_id, timestamp, event_type))"
            )
            status_columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(control_status)")
            }
            for name, definition in (
                ("candidate_json", "TEXT"),
                ("policy_mode", "TEXT"),
                ("policy_limit_c", "REAL"),
                ("last_command_at", "REAL"),
                ("command_sent", "INTEGER"),
            ):
                if name not in status_columns:
                    self.connection.execute(f"ALTER TABLE control_status ADD COLUMN {name} {definition}")
            sensor_columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(sensor_readings)")
            }
            if "smoothed_temperature_c" not in sensor_columns:
                self.connection.execute(
                    "ALTER TABLE sensor_readings ADD COLUMN smoothed_temperature_c REAL"
                )
            trial_columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(closed_loop_trial_samples)")
            }
            if "trial_note" not in trial_columns:
                self.connection.execute(
                    "ALTER TABLE closed_loop_trial_samples ADD COLUMN trial_note TEXT"
                )

    def write(self, sample: SensorSample, result: InferenceResult, reason: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO control_events_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sample.timestamp, sample.temperature_c, result.predicted_temp_c, result.intake_pwm,
                 result.exhaust_pwm, result.recovery_seconds, reason),
            )

    def write_sensor(self, sample: SensorSample, smoothed_temperature_c: float | None) -> None:
        """수신 원본을 저장해 Streamlit이 제어 프로세스와 분리되어도 볼 수 있게 합니다."""
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO sensor_readings "
                "(timestamp, temperature_c, humidity_pct, pm25_ug_m3, heat_load, intake_rpm, "
                "exhaust_rpm, intake_pwm, exhaust_pwm, smoothed_temperature_c) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sample.timestamp, sample.temperature_c, sample.humidity_pct, sample.pm25_ug_m3,
                 sample.heat_load, sample.intake_rpm, sample.exhaust_rpm,
                 sample.intake_pwm, sample.exhaust_pwm, smoothed_temperature_c),
            )

    def write_status(
        self,
        sample: SensorSample,
        result: InferenceResult,
        reason: str,
        last_command_at: float,
        command_sent: bool,
    ) -> None:
        """대시보드용 현재 제어 상태입니다. PWM 변경 여부와 관계없이 항상 최신화합니다."""
        comparison = [
            {
                "intake_pwm": int(candidate.intake_pwm),
                "exhaust_pwm": int(candidate.exhaust_pwm),
                "predicted_temp_c": float(round(candidate.predicted_temp_c, 3)),
                "relative_energy": float(round(candidate.relative_energy, 4)),
                "recovery_seconds": candidate.recovery_seconds,
                "total_relative_energy": round(candidate.total_relative_energy, 4) if candidate.total_relative_energy is not None else None,
                "safe": bool(candidate.safe),
                "selected": bool(candidate.selected),
                'control_cost':candidate.control_cost,
                'predicted_rate_c_per_s':candidate.predicted_rate_c_per_s,
                'upper_temp_c':candidate.upper_temp_c,
            }
            for candidate in result.candidate_comparison
        ]
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO control_status "
                "(id, timestamp, temperature_c, predicted_temp_c, intake_percent, exhaust_percent, "
                "recovery_seconds, model_name, reason, candidate_json, policy_mode, policy_limit_c, "
                "last_command_at, command_sent) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sample.timestamp, sample.temperature_c, result.predicted_temp_c,
                 int(result.intake_pwm), int(result.exhaust_pwm),
                 int(result.recovery_seconds) if result.recovery_seconds is not None else None,
                 result.model_name, reason, json.dumps(comparison), result.policy_mode,
                 result.policy_limit_c, last_command_at, int(command_sent)),
            )

    def write_trial_sample(
        self,
        trial: TrialConfig | None,
        sample: SensorSample,
        result: InferenceResult,
        reason: str,
        command_intake_pwm: int,
        command_exhaust_pwm: int,
        command_sent: bool,
    ) -> None:
        """폐루프 시험의 입력·명령·피드백을 같은 시각 행으로 보존합니다."""
        if trial is None:
            return
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO closed_loop_trial_samples "
                "(trial_id, timestamp, temperature_c, predicted_temp_c, command_intake_pwm, "
                "command_exhaust_pwm, feedback_intake_pwm, feedback_exhaust_pwm, intake_rpm, "
                "exhaust_rpm, model_name, policy_mode, trial_note, reason, command_sent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trial.trial_id, sample.timestamp, sample.temperature_c, result.predicted_temp_c,
                 command_intake_pwm, command_exhaust_pwm, sample.intake_pwm, sample.exhaust_pwm,
                 sample.intake_rpm, sample.exhaust_rpm, result.model_name, result.policy_mode,
                 trial.note, reason, int(command_sent)),
            )

    def write_trial_event(
        self, trial: TrialConfig | None, event_type: str, note: str | None = None
    ) -> None:
        """가열 시작·종료처럼 센서가 알 수 없는 수동 시험 사건을 남깁니다."""
        if trial is None:
            return
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO closed_loop_trial_events VALUES (?, ?, ?, ?)",
                (trial.trial_id, time.time(), event_type, note),
            )

    def close(self) -> None:
        self.connection.close()


class AirGuardRuntime:
    def __init__(self, args: argparse.Namespace):
        self.stop_event = threading.Event()
        self.state = RuntimeState()
        self.state_lock = threading.Lock()
        # 가상 센서는 실제 ESP32에 명령을 보내면 안 되므로 항상 전송 없이 실행합니다.
        self.fans = EspFanController(dry_run=args.dry_run or args.dummy)
        self.events = EventStore(Path(args.database))
        self.period = args.period
        self.target_c = args.target_temp
        self.emergency_temp_c = args.emergency_temp
        self.rapid_rise_c_per_s = args.rapid_rise_c_per_s
        if self.emergency_temp_c <= self.target_c:
            raise ValueError("--emergency-temp은 --target-temp보다 커야 합니다.")
        self.trial = TrialConfig(args.trial_id, args.trial_note) if args.trial_id else None
        self.trial_finished = False
        self.baseline_trigger_c = args.baseline_trigger if args.baseline_trigger is not None else self.target_c
        self.controller = self._load_controller(args)
        self.control_policy_version=getattr(args,'control_policy','auto')
        if self.control_policy_version=='auto':
            self.control_policy_version=getattr(self.controller,'default_control_policy','v4')
        if (getattr(self.controller,'context_feature_count',0)==8)!=(self.control_policy_version=='v6'):
            raise ValueError('v6 궤적 모델과 v6 제어 정책은 함께 사용해야 합니다.')
        if args.raw_rise_assist is None:
            args.raw_rise_assist=getattr(self.controller,'recommended_raw_rise_assist',False)
        self.policy_v5=HysteresisPolicy(self.target_c)
        if self.controller is not None:
            self.controller.control_policy_version=self.control_policy_version
        required_window = (
            self.controller.sequence_length if isinstance(
                self.controller, (LstmMpcController, MultiHorizonLstmMpcController)
            )
            else 12 if self.controller is not None else 2
        )
        window_size = required_window if args.window is None else args.window
        if window_size < required_window:
            raise ValueError(
                f"--window {window_size}은(는) 선택 LSTM의 입력 길이 "
                f"{required_window}보다 짧습니다."
            )
        self.buffer = SensorBuffer(window_size=window_size, sample_period_s=args.period)
        self.switch_gate = EnergySwitchGate(args.min_saving_ratio, args.min_saving_absolute,
                                            args.switch_confirmations, args.min_hold_seconds)
        if self.control_policy_version=='v5':
            self.switch_gate=SafeEnergySwitchGate(args.min_saving_ratio,args.min_saving_absolute,
                                                args.switch_confirmations,args.min_hold_seconds)
        if self.control_policy_version=='v6':
            from trajectory_control_v6 import TrajectorySwitchGate
            self.switch_gate=TrajectorySwitchGate(args.min_saving_ratio,args.min_saving_absolute,
                                                  args.switch_confirmations,args.min_hold_seconds)
        if args.rate_weight is not None and self.controller is not None:
            self.controller.rate_weight=args.rate_weight
        self.last_pwm_change = float('-inf')
        self.lstm_stability = PwmStabilityGuard(target_c=self.target_c) if args.stabilize_lstm and isinstance(self.controller, LstmMpcController) else None
        if args.raw_rise_assist and self.controller is None:
            raise ValueError('--raw-rise-assist는 threshold 비교 모드에 적용하지 않습니다.')
        self.raw_rise_assist = RawRiseAssist(self.target_c,args.raw_rise_rate,args.raw_rise_hold) if args.raw_rise_assist else None
        self.record_runtime_metadata(args, window_size)

    def record_runtime_metadata(self, args: argparse.Namespace, window_size: int) -> None:
        """시험 이름과 실제 로드 모델이 달라지는 혼선을 막습니다."""
        metadata = {
            'trial_id': args.trial_id,
            'controller': args.controller_model,
            'control_policy_resolved':self.control_policy_version,
            'model_path': getattr(self.controller, 'model_path', None),
            'sha256': getattr(self.controller, 'model_sha256', None),
            'horizon_seconds': getattr(self.controller, 'horizon_seconds', None),
            'window_size': window_size,
            'arguments': vars(args),
        }
        print('[MODEL] ' + json.dumps(metadata, ensure_ascii=False))
        with self.events.lock, self.events.connection:
            self.events.connection.execute(
                'CREATE TABLE IF NOT EXISTS runtime_metadata '
                '(timestamp REAL PRIMARY KEY, trial_id TEXT, metadata_json TEXT NOT NULL)'
            )
            self.events.connection.execute(
                'INSERT INTO runtime_metadata VALUES (?, ?, ?)',
                (time.time(), args.trial_id, json.dumps(metadata, ensure_ascii=False)),
            )

    def _load_controller(self, args: argparse.Namespace):
        if args.controller_model == "threshold":
            return None
        model_dir = Path(args.models)
        if args.controller_model == "physics":
            model_path = model_dir / "physics_mpc_model.pkl"
            if not model_path.is_file():
                raise FileNotFoundError(
                    f"Physics 모델 파일이 없습니다: {model_path}. "
                    "train_model.py를 --models 경로와 같은 위치로 다시 실행하세요."
                )
            return PhysicsMpcController(model_path, self.target_c, args.horizon, args.min_pwm)
        if args.controller_model == "lstm-multi":
            model_path = model_dir / "lstm_multi_horizon_temperature_model.pt"
            if not model_path.is_file():
                raise FileNotFoundError(f"다중 예측 LSTM 파일이 없습니다: {model_path}")
            return MultiHorizonLstmMpcController(
                model_path, self.target_c, args.min_pwm, args.multi_lstm_horizon
            )
        model_path = model_dir / args.lstm_model_file
        if not model_path.is_file():
            raise FileNotFoundError(
                f"LSTM 모델 파일이 없습니다: {model_path}. "
                    "train_model.py를 --models models로 다시 실행하세요."
            )
        return LstmMpcController(model_path, self.target_c, args.min_pwm)

    def receive_sample(self, sample: SensorSample) -> None:
        self.buffer.append(sample)
        self.events.write_sensor(sample, self.buffer.latest_smoothed_temperature())
        with self.state_lock:
            self.state.latest_sample = sample

    def bind_serial_fan_sender(self, send_command) -> None:
        """센서 수신기와 같은 USB Serial 연결을 팬 명령에도 사용합니다."""
        self.fans.bind_serial_sender(send_command)

    def record_trial_event(self, event_type: str, note: str | None = None) -> None:
        """터미널 입력으로 기록한 시험 사건을 SQLite에 저장합니다."""
        if self.trial is None:
            print("[EVENT] --trial-id가 없어 시험 사건을 저장하지 않았습니다.")
            return
        self.events.write_trial_event(self.trial, event_type, note)
        if event_type == "trial_end":
            self.trial_finished = True
        detail = f" · {note}" if note else ""
        print(f"[EVENT] {self.trial.trial_id}: {event_type}{detail}")

    def finish_trial(self, note: str = "main_runner stopped") -> None:
        if self.trial is not None and not self.trial_finished:
            self.record_trial_event("trial_end", note)

    def _control_policy(
        self, history: np.ndarray
    ) -> tuple[str, float, float, list[tuple[int, int]]]:
        """현재 온도와 선택 모델의 예측으로 운전 후보를 정합니다."""
        current_temp = float(history[-1, 0])
        projected_temp = self.controller.forecast_current(history)
        if self.control_policy_version=='v6':
            pair=(self.state.last_intake_percent,self.state.last_exhaust_percent)
            self.controller.current_command_pair=pair
            return '상한·상승률·에너지',self.target_c,projected_temp,list(self.controller.pwm_candidates)
        if self.control_policy_version=='v5':
            stage,limit=self.policy_v5.update(current_temp,projected_temp)
            candidates=candidates_for_stage(self.controller.pwm_candidates,stage)
            pair=(self.state.last_intake_percent,self.state.last_exhaust_percent)
            if pair in self.controller.pwm_candidates and pair not in candidates:
                candidates=candidates+[pair]
            self.controller.current_command_pair=pair
            return stage,limit,projected_temp,candidates
        risk_temp = max(current_temp, projected_temp)
        if risk_temp >= self.target_c:
            stage, limit = "상한 회복", self.target_c
        elif risk_temp >= PREVENTIVE_LIMIT_C:
            stage, limit = "상한 보호", self.target_c
        elif risk_temp >= ECO_LIMIT_C:
            stage, limit = "예방 냉각", PREVENTIVE_LIMIT_C
        else:
            stage, limit = "절전 유지", ECO_LIMIT_C
        return stage, limit, projected_temp, candidates_for_stage(self.controller.pwm_candidates, stage)

    def _requires_emergency_cooling(
        self, current_temp: float, projected_temp: float, rate_c_per_s: float
    ) -> bool:
        """목표 상한이 아닌 실제 위험 상태에서만 최대 환기를 사용합니다."""
        if current_temp >= self.emergency_temp_c:
            return True
        if current_temp >= PREVENTIVE_LIMIT_C and projected_temp >= self.emergency_temp_c:
            return True
        return current_temp >= self.target_c and rate_c_per_s >= self.rapid_rise_c_per_s

    def run(self) -> None:
        next_tick = time.monotonic() + self.period
        while not self.stop_event.wait(max(0.0, next_tick - time.monotonic())):
            started = time.monotonic()
            next_tick = started + self.period
            sample = self.buffer.latest()
            if sample is None:
                continue
            print(f"[SENSOR] 현재 온도: {sample.temperature_c:.2f}°C")
            if time.time() - sample.timestamp > SENSOR_TIMEOUT_S:
                self._apply_safe(sample, "센서 데이터 시간 초과")
                continue
            if sample.temperature_c >= self.emergency_temp_c:
                self._apply_emergency_cooling(sample, "실제 온도 비상 한계 도달")
                continue
            if self.raw_rise_assist is not None:
                raw_samples = self.buffer.snapshot()
                rise = self.raw_rise_assist.update(raw_samples)
                if rise.active and (self.control_policy_version not in ('v5','v6') or sample.temperature_c>=self.target_c or
                                    (sample.temperature_c>=self.target_c-.5 and rise.projected_temp_c>=self.target_c)):
                    smoothed_rate = 0.
                    if len(raw_samples) >= 4:
                        recent = [s.temperature_c for s in raw_samples[-4:]]
                        dt = raw_samples[-1].timestamp-raw_samples[-2].timestamp
                        if dt > 0:
                            smoothed_rate = (float(np.median(recent[1:]))-float(np.median(recent[:3])))/dt
                    if sample.temperature_c >= self.target_c and max(rise.rate_1s,smoothed_rate) >= self.rapid_rise_c_per_s:
                        self._apply_emergency_cooling(sample,'원본 상승률: 실제 상한 이상 급상승')
                        continue
                    with self.state_lock:
                        previous = (self.state.last_intake_percent, self.state.last_exhaust_percent)
                    floor = (60,100) if sample.temperature_c >= PREVENTIVE_LIMIT_C else (35,60)
                    self.switch_gate.reset()
                    if self.control_policy_version=='v6':self.controller.upper_risk_count=0
                    self._apply(
                        sample,
                        InferenceResult(
                            rise.projected_temp_c, max(floor[0],previous[0]), max(floor[1],previous[1]),
                            None, 'Raw rise assist · 5s linear estimate',
                            policy_mode='원본 상승률 예방 냉각',policy_limit_c=self.target_c,
                        ),
                        f'원본 상승률 보조: 1/3/5초 {rise.rate_1s:.3f}/{rise.rate_3s:.3f}/{rise.rate_5s:.3f}°C/s · 5초 선형 외삽(모델 추론 아님)',
                    )
                    continue
            if not self.buffer.is_ready():
                if sample.temperature_c >= self.target_c:
                    self._apply(
                        sample,
                        InferenceResult(sample.temperature_c, 60, 100, None, "warmup protection"),
                        "시계열 버퍼 수집 중: 상한 회복 조합 적용",
                    )
                else:
                    self._apply(sample, InferenceResult(sample.temperature_c, IDLE_PWM, IDLE_PWM, None, "warmup"), "시계열 버퍼 수집 중")
                continue
            with self.state_lock:
                previous_intake = max(0, self.state.last_intake_percent)
                previous_exhaust = max(0, self.state.last_exhaust_percent)
            history = self.buffer.control_feature_matrix(
                previous_intake, previous_exhaust,
                include_raw_rates=getattr(self.controller,'context_feature_count',4)>=7,
            )
            if getattr(self.controller,'context_feature_count',4)==8:
                history=np.column_stack((history,history[:,0]))
            if self.controller is None:
                self._apply_threshold_baseline(sample)
                continue
            policy_mode, policy_limit, projected_temp, allowed_candidates = self._control_policy(history)
            if self._requires_emergency_cooling(
                sample.temperature_c, projected_temp, float(history[-1, 1])
            ):
                self._apply_emergency_cooling(
                    sample,
                    f"비상 조건: 현재 {sample.temperature_c:.2f}°C, "
                    f"예측 {projected_temp:.2f}°C, 변화율 {history[-1, 1]:.3f}°C/s",
                )
                continue
            result = replace(
                self.controller.choose(history, policy_limit, allowed_candidates),
                policy_mode=policy_mode,
                policy_limit_c=policy_limit,
            )
            reason = f"{policy_mode}: 현재 PWM 유지 시 {projected_temp:.2f}°C 예측"
            current = next((item for item in result.all_candidates
                            if (item.intake_pwm, item.exhaust_pwm) == (previous_intake, previous_exhaust)), None)
            proposed = next(item for item in result.all_candidates
                            if (item.intake_pwm, item.exhaust_pwm) == (result.intake_pwm, result.exhaust_pwm))
            risk_options={}
            if self.control_policy_version=='v5':
                risk_options['upper_risk']=self.policy_v5.upper_risk(sample.temperature_c,projected_temp)
            elif self.control_policy_version=='v6':
                risk_options['upper_risk']=self.controller.upper_risk(history,sample.temperature_c)
            chosen, switch_reason = self.switch_gate.decide(
                current, proposed, time.monotonic(), self.last_pwm_change, self.controller.horizon_seconds,**risk_options)
            result = replace(result, intake_pwm=chosen.intake_pwm, exhaust_pwm=chosen.exhaust_pwm,
                             predicted_temp_c=chosen.predicted_temp_c, recovery_seconds=chosen.recovery_seconds,
                             candidate_comparison=candidate_summary(list(result.all_candidates), chosen))
            reason += " · " + switch_reason
            if self.lstm_stability is not None:
                proposed_pair = (result.intake_pwm, result.exhaust_pwm)
                current_pair = (previous_intake, previous_exhaust)
                actual_current_forecast = self.controller.forecast(history, *current_pair)
                pair, detail = self.lstm_stability.decide(
                    current_pair, proposed_pair, sample.temperature_c,
                    float(history[-1, 1]), actual_current_forecast, time.monotonic(),
                )
                if pair != proposed_pair:
                    kept = CandidateComparison(
                        *pair, actual_current_forecast, relative_fan_energy(*pair),
                        actual_current_forecast <= policy_limit, selected=True,
                    )
                    displayed = [replace(item, selected=False) for item in result.candidate_comparison
                                 if (item.intake_pwm, item.exhaust_pwm) != pair]
                    result = replace(
                        result, intake_pwm=pair[0], exhaust_pwm=pair[1],
                        predicted_temp_c=actual_current_forecast, recovery_seconds=None,
                        candidate_comparison=tuple(displayed+[kept]),
                    )
                reason += ' · ' + detail
            self._apply(sample, result, reason)
            elapsed = time.monotonic() - started
            if elapsed > self.period:
                print(
                    f"[WARNING] 제어 처리 지연: {elapsed:.3f}초 "
                    f"(목표 주기 {self.period:.2f}초)"
                )

    def _apply_threshold_baseline(self, sample: SensorSample) -> None:
        """예측 없이 현재 온도만 보는 비교 기준 제어입니다.

        상한 도달 시의 100/100 명령은 run()의 안전 규칙에서 처리됩니다.
        """
        active = sample.temperature_c >= self.baseline_trigger_c
        intake, exhaust = (100, 100) if active else (IDLE_PWM, IDLE_PWM)
        relation = "≥" if active else "<"
        reason = (
            f"반응형 기준: 현재 온도 {sample.temperature_c:.2f}°C "
            f"{relation} {self.baseline_trigger_c:.1f}°C"
        )
        self._apply(
            sample,
            InferenceResult(sample.temperature_c, intake, exhaust, None,
                            "Threshold baseline", policy_mode="반응형 기준",
                            policy_limit_c=self.baseline_trigger_c),
            reason,
        )

    def _apply_safe(self, sample: SensorSample, reason: str) -> None:
        self.switch_gate.reset()
        self._apply(sample, InferenceResult(sample.temperature_c, IDLE_PWM, IDLE_PWM, None, "sensor safety"), reason)

    def _apply_emergency_cooling(self, sample: SensorSample, condition: str) -> None:
        self.switch_gate.reset()
        reason = (
            f"{condition}: 100/100 최대 환기 "
            f"(목표 {self.target_c:.1f}°C, 비상 {self.emergency_temp_c:.1f}°C)"
        )
        self._apply(
            sample, InferenceResult(sample.temperature_c, 100, 100, None, "emergency cooling"), reason,
        )

    def _apply(self, sample: SensorSample, result: InferenceResult, reason: str) -> None:
        now = time.time()
        with self.state_lock:
            old = (self.state.last_intake_percent, self.state.last_exhaust_percent)
            new = (result.intake_pwm, result.exhaust_pwm)
            changed = new != old
            due = now - self.state.last_command_at >= COMMAND_HEARTBEAT_S
        command_sent = False
        if changed or due:
            command = self.fans.apply(result.intake_pwm, result.exhaust_pwm, reason)
            with self.state_lock:
                self.state.last_command_at = command.timestamp
                self.state.last_intake_percent = command.intake_percent
                self.state.last_exhaust_percent = command.exhaust_percent
            command_sent = True
            if changed:
                self.last_pwm_change = time.monotonic()
                recovery = "계산 불가" if result.recovery_seconds is None else f"약 {result.recovery_seconds}초"
                print(
                    f"[CONTROL] {result.model_name}: PWM 흡기/배기 {old[0]}%/{old[1]}% → "
                    f"{result.intake_pwm}%/{result.exhaust_pwm}% | {reason} | "
                    f"예측 {result.predicted_temp_c:.2f}°C | {self.target_c:.1f}°C 회복 예상 {recovery}"
                )
                self.events.write(sample, result, reason)
        with self.state_lock:
            last_command_at = self.state.last_command_at
            command_intake = self.state.last_intake_percent
            command_exhaust = self.state.last_exhaust_percent
        self.events.write_status(sample, result, reason, last_command_at, command_sent)
        self.events.write_trial_sample(
            self.trial, sample, result, reason, command_intake, command_exhaust, command_sent
        )

    def stop_fans(self) -> None:
        """제어 루프를 멈춘 뒤 ESP32에 팬 정지 명령을 한 번 보냅니다."""
        try:
            command = self.fans.apply(0, 0, "프로그램 종료: 팬 정지")
        except (ConnectionError, RuntimeError) as error:
            print(f"[SHUTDOWN] 팬 정지 명령을 보내지 못했습니다: {error}")
            return
        with self.state_lock:
            self.state.last_command_at = command.timestamp
            self.state.last_intake_percent = 0
            self.state.last_exhaust_percent = 0
        print("[SHUTDOWN] 팬 정지 명령 전송: 흡기 0% / 배기 0%")

    def close(self) -> None:
        self.stop_event.set()
        self.events.close()


class TerminalTrialRecorder:
    """시험 중 Enter 기반 명령으로 가열 사건을 남기는 보조 스레드입니다."""

    def __init__(self, runtime: AirGuardRuntime):
        self.runtime = runtime
        self.heating = False

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="trial-keyboard", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        print("[EVENT] 입력: h(가열 토글), on, off, n 메모, q(시험 종료)")
        while not self.runtime.stop_event.is_set():
            try:
                command = input().strip()
            except EOFError:
                print("[EVENT] 표준 입력이 닫혀 키보드 기록을 종료합니다.")
                return
            if not command:
                continue
            lowered = command.lower()
            if lowered in {"h", "heat"}:
                self.heating = not self.heating
                self.runtime.record_trial_event("heat_on" if self.heating else "heat_off")
            elif lowered in {"on", "heat_on"}:
                self.heating = True
                self.runtime.record_trial_event("heat_on")
            elif lowered in {"off", "heat_off"}:
                self.heating = False
                self.runtime.record_trial_event("heat_off")
            elif lowered.startswith("n ") or lowered.startswith("note "):
                note = command.split(maxsplit=1)[1]
                self.runtime.record_trial_event("note", note)
            elif lowered in {"q", "quit", "end"}:
                if self.heating:
                    self.heating = False
                    self.runtime.record_trial_event("heat_off", "terminal quit")
                self.runtime.record_trial_event("trial_end", "terminal quit")
                self.runtime.stop_event.set()
                return
            else:
                print("[EVENT] 알 수 없는 입력입니다: h, on, off, n 메모, q")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-port", default=None, help="ESP32 USB 포트. 생략 시 /dev/serial/by-id를 우선 탐색")
    parser.add_argument("--serial-baud", type=int, default=115200)
    parser.add_argument("--models", default=str(MODELS_DIR))
    parser.add_argument("--lstm-model-file", default="lstm_temperature_model.pt",
                        help="불러올 LSTM 파일명 또는 절대 경로")
    parser.add_argument("--stabilize-lstm", action="store_true",
                        help="시험용 LSTM PWM 안정화: 예측 위험 2회, 일반 변경 3회 및 8초 유지 확인")
    assistance=parser.add_mutually_exclusive_group()
    assistance.add_argument('--raw-rise-assist',dest='raw_rise_assist',action='store_true',help='원본 상승률 보조 사용')
    assistance.add_argument('--no-raw-rise-assist',dest='raw_rise_assist',action='store_false',help='모델 권장값과 무관하게 원본 상승률 보조 해제')
    parser.set_defaults(raw_rise_assist=None)
    parser.add_argument('--raw-rise-rate',type=float,default=.08,help='원본 1초 상승률 기동 기준(°C/s), 시험 기본값 0.08')
    parser.add_argument('--raw-rise-hold',type=float,default=3.,help='원본 상승률 보조 최소 유지시간(초)')
    parser.add_argument("--database", default=str(PROJECT_ROOT/'runtime/edge_events.db'))
    parser.add_argument("--controller-model", choices=("physics", "lstm", "lstm-multi", "threshold"), default="physics")
    parser.add_argument('--control-policy',choices=('auto','v4','v5','v6'),default='auto',help='auto: 모델 메타데이터의 정책 사용')
    parser.add_argument('--rate-weight',type=float,default=None,help='v6 양의 상승률 억제 가중치. 기본은 모델 설정')
    parser.add_argument("--multi-lstm-horizon", type=int, default=5,
                        help="lstm-multi에서 제어 기준으로 사용할 예측 시간(기본 5초)")
    parser.add_argument("--target-temp", type=float, default=TARGET_TEMPERATURE_C)
    parser.add_argument("--emergency-temp", type=float, default=28.0,
                        help="최대 환기(100/100)를 시작하는 비상 온도(°C)")
    parser.add_argument("--rapid-rise-c-per-s", type=float, default=0.15,
                        help="목표 온도 이상에서 최대 환기를 시작하는 급상승 변화율(°C/s)")
    parser.add_argument("--window", type=int, default=None,
                        help="센서 버퍼 행 수. 생략 시 LSTM artifact의 입력 길이, Physics는 12")
    parser.add_argument("--horizon", type=int, default=10, help="Physics MPC 예측 시간(초)")
    parser.add_argument("--min-pwm", type=int, choices=range(MINIMUM_PWM, 101), default=MINIMUM_PWM,
                        help="팬이 켜질 때 허용하는 최소 PWM(검증 기본값 35%%)")
    parser.add_argument("--period", type=float, default=1.0, help="센서·제어 주기(초)")
    parser.add_argument("--min-saving-ratio", type=float, default=0.10, help="변경에 필요한 최소 예상 절감 비율")
    parser.add_argument("--min-saving-absolute", type=float, default=0.0, help="최소 절감 점수(PWM³×초, Wh 아님)")
    parser.add_argument("--switch-confirmations", type=int, default=5, help="동일 후보의 연속 우위 확인 횟수")
    parser.add_argument("--min-hold-seconds", type=float, default=15.0, help="안전 운전 중 PWM 최소 유지시간")
    parser.add_argument("--dummy", action="store_true", help="USB Serial 없이 가상 센서로 시험")
    parser.add_argument("--dry-run", action="store_true", help="팬 명령을 전송하지 않고 출력만 표시")
    parser.add_argument("--trial-id", help="실제 연결 제어 시험 식별자. 지정하면 closed_loop_trial_samples에 기록")
    parser.add_argument("--trial-note", help="가열 시간·거리·순서 등 시험 조건 메모")
    parser.add_argument("--terminal-events", action="store_true",
                        help="시험 중 터미널 h/on/off/n 메모/q 입력으로 가열 사건을 기록")
    parser.add_argument("--baseline-trigger", type=float, default=None,
                        help="threshold 비교 기준의 팬 상향 온도. 기본값은 --target-temp")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.terminal_events and not args.trial_id:
        raise ValueError("--terminal-events에는 --trial-id가 필요합니다.")
    runtime = AirGuardRuntime(args)
    receiver = DummySensorPublisher(runtime.receive_sample, runtime.stop_event) if args.dummy else SerialSensorReceiver(
        SerialConfig(args.serial_port, args.serial_baud), runtime.receive_sample
    )
    if not args.dummy:
        runtime.bind_serial_fan_sender(receiver.send_command)
    keyboard = TerminalTrialRecorder(runtime) if args.terminal_events else None
    control_thread: threading.Thread | None = None
    try:
        receiver.start()
        runtime.record_trial_event("trial_start", args.trial_note)
        control_thread = threading.Thread(target=runtime.run, name="control-loop", daemon=True)
        control_thread.start()
        if keyboard is not None:
            keyboard.start()
        while not runtime.stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        print("종료 요청을 받았습니다.")
    finally:
        runtime.stop_event.set()
        if control_thread is not None and control_thread.is_alive():
            control_thread.join(timeout=args.period + 1.0)
        runtime.stop_fans()
        runtime.finish_trial()
        runtime.close()
        if not args.dummy:
            receiver.stop()


if __name__ == "__main__":
    main()
