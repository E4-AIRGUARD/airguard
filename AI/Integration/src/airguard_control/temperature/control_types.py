"""온도 판단 결과. 다른 모듈과 팬 명령을 중재할 때 사용하는 데이터입니다."""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CandidateComparison:
    intake_pwm: int
    exhaust_pwm: int
    predicted_temp_c: float
    relative_energy: float  # 호환 필드: 추정 전력 W
    safe: bool
    selected: bool = False
    recovery_seconds: int | None = None
    total_relative_energy: float | None = None  # 호환 필드: 예상 에너지 J
    control_cost: float | None = None
    predicted_rate_c_per_s: float | None = None
    upper_temp_c: float | None = None


@dataclass(frozen=True)
class InferenceResult:
    predicted_temp_c: float
    intake_pwm: int
    exhaust_pwm: int
    recovery_seconds: int | None
    model_name: str
    candidate_comparison: tuple[CandidateComparison, ...] = ()
    policy_mode: str = "대기"
    policy_limit_c: float | None = None
    all_candidates: tuple[CandidateComparison, ...] = ()


def candidate_summary(candidates, selected):
    safe = [candidate for candidate in candidates if candidate.safe]
    unsafe = [candidate for candidate in candidates if not candidate.safe]
    displayed = safe[:5] + unsafe[:1]
    if selected not in displayed:
        displayed.append(selected)
    ordered = sorted(displayed, key=lambda c: float('inf') if c.total_relative_energy is None else c.total_relative_energy)
    return tuple(replace(candidate, selected=candidate == selected) for candidate in ordered)
