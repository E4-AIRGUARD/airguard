"""Upper-limit-first, positive-rate suppression and energy-aware candidate control."""
import math
import numpy as np


class TrajectorySwitchGate:
    def __init__(self,ratio=.1,absolute=0.,confirmations=5,hold_seconds=15.):
        self.ratio=ratio;self.absolute=absolute;self.confirmations=confirmations;self.hold_seconds=hold_seconds
        self.reset()

    def reset(self):
        self.pending=None;self.count=0

    def decide(self,current,proposed,now,last_change,maintenance_seconds,upper_risk=False):
        if current is None:
            self.reset();return proposed,'초기 궤적 후보 선택'
        key=(proposed.intake_pwm,proposed.exhaust_pwm)
        if key==(current.intake_pwm,current.exhaust_pwm):
            self.reset();return current,'궤적·상승률·에너지 기준 현재 조합 유지'
        improved_safety=(proposed.safe and not current.safe) or (
            math.isfinite(proposed.upper_temp_c) and proposed.upper_temp_c<current.upper_temp_c-.02)
        improved_rise=(proposed.predicted_rate_c_per_s<current.predicted_rate_c_per_s-.02
                       and proposed.upper_temp_c<=current.upper_temp_c+.02)
        if upper_risk and (improved_safety or improved_rise):
            self.reset();return proposed,'상한 위험 확인: 궤적/상승률 개선 후보 우선 적용'
        if not proposed.safe:
            self.reset();return current,'상한 여유 조건 미확보: 현재 조합 유지'
        old,new=current.control_cost,proposed.control_cost
        if not all(v is not None and math.isfinite(v) for v in (old,new)):
            self.reset();return current,'목적함수 불확실: 유지'
        if old-new<=max(self.ratio*max(old,0),self.absolute):
            self.reset();return current,'상승률·에너지 개선 이득 부족: 유지'
        self.count=self.count+1 if self.pending==key else 1;self.pending=key
        if self.count<self.confirmations or now-last_change<self.hold_seconds:
            return current,f'목적함수 개선 확인 {self.count}/{self.confirmations}: 유지시간 적용'
        self.reset();return proposed,'상승률·에너지·변경 비용 개선 확인 후 변경'


def choose_trajectory(controller,history,allowed):
    from main_runner import CandidateComparison,InferenceResult,candidate_summary,relative_fan_energy
    pairs=allowed or controller.pwm_candidates
    current=getattr(controller,'current_command_pair',None)
    rows=[]
    for intake,exhaust in pairs:
        detail=controller.trajectory(history,intake,exhaust)
        temperatures=np.asarray(detail['temperatures']);rate=float(detail['rate'])
        if not (np.isfinite(temperatures).all() and math.isfinite(rate)
                and math.isfinite(float(detail['max_rate']))):
            rows.append(CandidateComparison(intake,exhaust,float('nan'),relative_fan_energy(intake,exhaust),
                        False,control_cost=float('inf'),predicted_rate_c_per_s=float('inf'),
                        upper_temp_c=float('inf')))
            continue
        upper=float(max(float(history[-1,0]),np.max(temperatures+controller.prediction_margin)))
        power=relative_fan_energy(intake,exhaust)
        # Recovery is to the buffered upper limit, not a fixed 25.x setpoint.
        guard=controller.target_c-float(np.max(controller.prediction_margin))
        peak=float(max(float(history[-1,0]),np.max(temperatures)))
        if peak<guard:
            recovery=0
        elif rate<-.01:
            duration=5+max(float(temperatures[-1])-guard,0)/(-rate)
            recovery=int(np.ceil(duration)) if duration<=600 else None
        else:
            recovery=None
        energy=power*recovery if recovery is not None else None
        # When long recovery is unknown, only a labelled 5-second power proxy is available.
        energy_term=(power*max(5,recovery) if recovery is not None else power*5)/5
        positive=max(0.,float(detail['max_rate']))
        proximity=float(np.clip((upper-(controller.target_c-1.5))/1.5,0,1))
        switching=0. if current is None else sum(abs(v-w) for v,w in zip((intake,exhaust),current))/100
        cost=energy_term+controller.rate_weight*proximity*positive**2+.1*switching
        rows.append(CandidateComparison(intake,exhaust,float(temperatures[-1]),power,
                    bool(np.isfinite(upper) and upper<controller.target_c),recovery_seconds=recovery,
                    total_relative_energy=energy,control_cost=float(cost),
                    predicted_rate_c_per_s=positive,upper_temp_c=upper))
    feasible=[r for r in rows if r.safe]
    finite=[r for r in rows if math.isfinite(r.upper_temp_c)]
    if not finite:
        selected=next((r for r in rows if (r.intake_pwm,r.exhaust_pwm)==current),rows[0])
    else:
        selected=min(feasible,key=lambda r:r.control_cost) if feasible else min(finite,key=lambda r:(r.upper_temp_c,r.control_cost))
    rows.sort(key=lambda r:(not r.safe,r.control_cost))
    return InferenceResult(selected.predicted_temp_c,selected.intake_pwm,selected.exhaust_pwm,
                           selected.recovery_seconds,f'{controller.display_name} · 상한/상승률/에너지',
                           candidate_summary(rows,selected),all_candidates=tuple(rows))
