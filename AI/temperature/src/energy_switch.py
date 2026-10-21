"""안전한 운전 중 작은 에너지 차이로 PWM이 반복 변경되는 것을 막습니다."""

import math


class EnergySwitchGate:
    def __init__(self, saving_ratio=0.1, saving_absolute=0.0, confirmations=3, hold_seconds=5.0):
        if not 0 <= saving_ratio < 1 or not math.isfinite(saving_ratio):
            raise ValueError("saving_ratio는 0 이상 1 미만이어야 합니다.")
        if not math.isfinite(saving_absolute) or saving_absolute < 0:
            raise ValueError("saving_absolute은 유한한 음이 아닌 값이어야 합니다.")
        if confirmations < 1 or not math.isfinite(hold_seconds) or hold_seconds < 0:
            raise ValueError("확인 횟수는 1 이상, 유지시간은 유한한 음이 아닌 값이어야 합니다.")
        self.ratio = saving_ratio
        self.absolute = saving_absolute
        self.confirmations = confirmations
        self.hold_seconds = hold_seconds
        self.pending = None
        self.count = 0

    def reset(self):
        self.pending, self.count = None, 0

    def decide(self, current, proposed, now, last_change, maintenance_seconds):
        if current is None or not current.safe:
            self.reset()
            return proposed, "현재 조합 안전성 미확보: 변경 대기 우회"
        key = (proposed.intake_pwm, proposed.exhaust_pwm)
        if key == (current.intake_pwm, current.exhaust_pwm):
            self.reset()
            return current, "현재 조합이 최적: 유지"
        if not proposed.safe:
            self.reset()
            return current, "후보 안전성 미확보: 유지"
        # 이미 정상인 경우 두 후보 모두 같은 시간의 유지 에너지를 비교합니다.
        if current.recovery_seconds == 0 and proposed.recovery_seconds == 0:
            old_cost = current.relative_energy * maintenance_seconds
            new_cost = proposed.relative_energy * maintenance_seconds
        else:
            old_cost, new_cost = current.total_relative_energy, proposed.total_relative_energy
        if any(value is None or not math.isfinite(value) or value < 0 for value in (old_cost, new_cost)):
            self.reset()
            return current, "에너지 산정 불가: 유지"
        saving = old_cost - new_cost
        threshold = max(self.ratio * old_cost, self.absolute)
        if saving <= threshold:
            self.reset()
            return current, f"예상 절감량 {saving:.3f} ≤ 기준 {threshold:.3f}: 유지"
        self.count = self.count + 1 if self.pending == key else 1
        self.pending = key
        detail = f"예상 절감 {saving:.3f} ({saving / old_cost:.1%}), 확인 {self.count}/{self.confirmations}"
        if self.count < self.confirmations or now - last_change < self.hold_seconds:
            return current, detail + ": 연속 확인 또는 최소 유지시간 대기"
        self.reset()
        return proposed, detail + ": 변경"
