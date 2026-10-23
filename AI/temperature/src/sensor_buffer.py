"""센서 입력을 고정 간격의 짧은 시계열로 정리합니다."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from threading import Lock

import numpy as np
from temperature_features import raw_temperature_rates

MAX_INTERPOLATED_SAMPLES = 1


@dataclass(frozen=True)
class SensorSample:
    timestamp: float
    temperature_c: float
    humidity_pct: float | None = None
    pm25_ug_m3: float | None = None
    ambient_temp_c: float | None = None
    heat_load: int = 0
    pressure_pa: float | None = None
    intake_rpm: float | None = None
    exhaust_rpm: float | None = None
    intake_pwm: float | None = None
    exhaust_pwm: float | None = None


class SensorBuffer:
    """원본 기록을 보존하고 모델 입력을 만들 때만 온도를 평활화합니다."""

    def __init__(
        self, window_size: int = 12, sample_period_s: float = 1.0, median_size: int = 3
    ):
        if window_size < 2:
            raise ValueError("window_size는 2 이상이어야 합니다.")
        if sample_period_s <= 0:
            raise ValueError("sample_period_s는 0보다 커야 합니다.")
        if median_size < 1:
            raise ValueError("median_size는 1 이상이어야 합니다.")
        self.window_size = window_size
        self.sample_period_s = sample_period_s
        self.median_size = median_size
        self._samples: deque[SensorSample] = deque(maxlen=window_size)
        self._lock = Lock()

    def append(self, sample: SensorSample) -> None:
        """원본 기록을 추가하고 1초짜리 결측만 선형 보간합니다."""
        if not np.isfinite(sample.timestamp):
            raise ValueError("timestamp는 유한한 숫자여야 합니다.")
        if not np.isfinite(sample.temperature_c):
            raise ValueError("temperature_c는 유한한 숫자여야 합니다.")
        if sample.heat_load not in (0, 1):
            raise ValueError("heat_load는 0 또는 1이어야 합니다.")

        with self._lock:
            if self._samples and sample.timestamp <= self._samples[-1].timestamp:
                return

            if self._samples:
                previous = self._samples[-1]
                self._insert_short_gap(previous, sample)

            self._samples.append(sample)

    def _insert_short_gap(self, previous: SensorSample, current: SensorSample) -> None:
        gap = current.timestamp - previous.timestamp
        missing = round(gap / self.sample_period_s) - 1
        if missing <= 0 or missing > MAX_INTERPOLATED_SAMPLES:
            return
        for index in range(1, missing + 1):
            ratio = index / (missing + 1)
            interpolated = replace(
                previous,
                timestamp=previous.timestamp + index * self.sample_period_s,
                temperature_c=previous.temperature_c + ratio * (current.temperature_c - previous.temperature_c),
                humidity_pct=_interpolate(previous.humidity_pct, current.humidity_pct, ratio),
                ambient_temp_c=_interpolate(previous.ambient_temp_c, current.ambient_temp_c, ratio),
                pressure_pa=_interpolate(previous.pressure_pa, current.pressure_pa, ratio),
            )
            self._samples.append(interpolated)

    def snapshot(self) -> list[SensorSample]:
        with self._lock:
            return list(self._samples)

    def latest(self) -> SensorSample | None:
        """안전 판단에 사용하는 가장 최근 원본 센서값입니다."""
        with self._lock:
            return self._samples[-1] if self._samples else None

    def latest_smoothed_temperature(self) -> float | None:
        """대시보드와 모델 확인용 최근 중앙값 온도입니다."""
        samples = self.snapshot()
        if not samples:
            return None
        return self._smoothed_temperatures(samples)[-1]

    def is_ready(self) -> bool:
        with self._lock:
            return len(self._samples) == self.window_size

    def control_feature_matrix(self, default_intake_pwm: float, default_exhaust_pwm: float, include_raw_rates: bool = False) -> np.ndarray:
        """온도 예측기용 입력: 온도, 최근 변화율, 실제 흡기·배기 PWM입니다.

        센서가 PWM을 보내지 않는 경우에는 마지막으로 보낸 팬 명령을 사용합니다.
        ``heat_load``는 실험 메타데이터로만 남기며 실시간 예측에는 사용하지 않습니다.
        """
        samples = self.snapshot()
        if len(samples) != self.window_size:
            raise RuntimeError("모델 추론 전에는 버퍼를 모두 채워야 합니다.")
        temperatures = np.asarray(self._smoothed_temperatures(samples), dtype=np.float64)
        # float32는 현재 Unix 시각에서 1초 간격을 구분하지 못합니다.
        times = np.array([item.timestamp for item in samples], dtype=np.float64)
        rates = np.diff(temperatures, prepend=temperatures[0]) / np.maximum(
            np.diff(times, prepend=times[0] - self.sample_period_s), 0.1
        )
        rows = [
            [
                temperature,
                rate,
                default_intake_pwm if item.intake_pwm is None else item.intake_pwm,
                default_exhaust_pwm if item.exhaust_pwm is None else item.exhaust_pwm,
            ]
            for item, temperature, rate in zip(samples, temperatures, rates)
        ]
        context = np.asarray(rows, dtype=np.float32)
        if include_raw_rates:
            raw_rates = raw_temperature_rates([s.temperature_c for s in samples], times)
            context = np.column_stack((context, raw_rates)).astype(np.float32)
        return context

    def _smoothed_temperatures(self, samples: list[SensorSample]) -> list[float]:
        recent: deque[float] = deque(maxlen=self.median_size)
        smoothed = []
        for sample in samples:
            recent.append(sample.temperature_c)
            smoothed.append(float(np.median(recent)))
        return smoothed


def _interpolate(left: float | None, right: float | None, ratio: float) -> float | None:
    if left is None or right is None:
        return left if right is None else right
    return left + ratio * (right - left)


