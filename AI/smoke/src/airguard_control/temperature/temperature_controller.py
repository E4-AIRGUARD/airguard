"""온도 판단만 담당합니다. Serial 전송과 DB 저장은 호출자가 수행합니다."""
from dataclasses import dataclass, replace
import time

import numpy as np

from airguard_control.temperature.control_types import InferenceResult, candidate_summary
from airguard_control.temperature.lstm_controller import LstmController
from airguard_control.temperature.raw_rise_assist import RawRiseAssist
from airguard_control.temperature.sensor_buffer import SensorBuffer, SensorSample
from airguard_control.temperature.trajectory_control import TrajectorySwitchGate


@dataclass(frozen=True)
class ControlConfig:
    target_c: float = 26.0
    emergency_c: float = 28.0
    rapid_rise_c_per_s: float = .15
    period_s: float = 1.0
    sensor_timeout_s: float = 5.0
    heartbeat_s: float = 5.0


class TemperatureController:
    def __init__(self, model_path, config=None):
        self.config = config or ControlConfig()
        self.model = LstmController(model_path, self.config.target_c)
        self.buffer = SensorBuffer(window_size=8, sample_period_s=self.config.period_s)
        self.rise_assist = RawRiseAssist(self.config.target_c, .08, 3.)
        self.switch_gate = TrajectorySwitchGate(.1, 0., 5, 15.)
        self.current_pair = (-1, -1)
        self.last_pwm_change = float('-inf')

    def observe(self, sample: SensorSample):
        self.buffer.append(sample)

    def confirm_applied(self, intake, exhaust, now=None):
        """최종 중재자가 실제 전송한 PWM을 알려야 유지시간이 일치합니다."""
        pair = (int(intake), int(exhaust))
        if pair != self.current_pair:
            self.last_pwm_change = time.monotonic() if now is None else now
        self.current_pair = pair

    def _fixed(self, sample, pair, source, reason, prediction=None):
        self.switch_gate.reset()
        return sample, InferenceResult(sample.temperature_c if prediction is None else prediction,
                                       *pair, None, source), reason

    def decide(self, wall_time=None, monotonic_time=None):
        """같은 판단에 사용한 (샘플, 결과, 사유)를 반환합니다. 장치는 조작하지 않습니다."""
        now = time.time() if wall_time is None else wall_time
        clock = time.monotonic() if monotonic_time is None else monotonic_time
        sample = self.buffer.latest()
        if sample is None:
            return None
        cfg = self.config
        if now - sample.timestamp > cfg.sensor_timeout_s:
            return self._fixed(sample, (35, 35), 'sensor safety', '센서 데이터 시간 초과')
        if sample.temperature_c >= cfg.emergency_c:
            return self._fixed(sample, (100, 100), 'emergency cooling', '실제 온도 비상 한계 도달')
        samples = self.buffer.snapshot()
        rise = self.rise_assist.update(samples)
        if rise.active and (sample.temperature_c >= cfg.target_c or (
                sample.temperature_c >= cfg.target_c - .5 and rise.projected_temp_c >= cfg.target_c)):
            smoothed_rate = 0.
            if len(samples) >= 4:
                recent = [s.temperature_c for s in samples[-4:]]
                dt = samples[-1].timestamp - samples[-2].timestamp
                if dt > 0:
                    smoothed_rate = (float(np.median(recent[1:])) - float(np.median(recent[:3]))) / dt
            if sample.temperature_c >= cfg.target_c and max(rise.rate_1s, smoothed_rate) >= cfg.rapid_rise_c_per_s:
                return self._fixed(sample, (100, 100), 'emergency cooling', '원본 상승률: 실제 상한 이상 급상승')
            self.model.upper_risk_count = 0
            floor = (60, 100) if sample.temperature_c >= 25.5 else (35, 60)
            pair = tuple(max(value, previous) for value, previous in zip(floor, self.current_pair))
            _, result, reason = self._fixed(sample, pair, 'Raw rise assist · 5s linear estimate',
                '원본 상승률 보조 · LSTM 추론 아님', rise.projected_temp_c)
            return sample, replace(result, policy_mode='원본 상승률 예방 냉각', policy_limit_c=cfg.target_c), reason
        if not self.buffer.is_ready():
            pair = (60, 100) if sample.temperature_c >= cfg.target_c else (35, 35)
            return self._fixed(sample, pair, 'warmup', '시계열 버퍼 수집 중')
        history = self.buffer.control_feature_matrix(*[max(0, v) for v in self.current_pair], include_raw_rates=True)
        history = np.column_stack((history, history[:, 0]))
        projected = self.model.forecast_current(history)
        if ((sample.temperature_c >= 25.5 and projected >= cfg.emergency_c)
                or (sample.temperature_c >= cfg.target_c and history[-1, 1] >= cfg.rapid_rise_c_per_s)):
            return self._fixed(sample, (100, 100), 'emergency cooling', '예측/급상승 비상 조건')
        self.model.current_command_pair = self.current_pair
        result = self.model.choose(history)
        current = next((c for c in result.all_candidates if (c.intake_pwm, c.exhaust_pwm) == self.current_pair), None)
        proposed = next(c for c in result.all_candidates if (c.intake_pwm, c.exhaust_pwm) == (result.intake_pwm, result.exhaust_pwm))
        chosen, reason = self.switch_gate.decide(current, proposed, clock, self.last_pwm_change,
            upper_risk=self.model.upper_risk(history, sample.temperature_c))
        result = replace(result, intake_pwm=chosen.intake_pwm, exhaust_pwm=chosen.exhaust_pwm,
            predicted_temp_c=chosen.predicted_temp_c, recovery_seconds=chosen.recovery_seconds,
            candidate_comparison=candidate_summary(list(result.all_candidates), chosen),
            policy_mode='상한·상승률·에너지', policy_limit_c=cfg.target_c)
        return sample, result, f'현재 PWM 유지 시 {projected:.2f}°C 예측 · {reason}'
