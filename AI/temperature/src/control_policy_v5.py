"""Separate policy preferences, upper-temperature risk and uncertain recovery."""
import math


class HysteresisPolicy:
    def __init__(self, target_c=26., margin_c=.2):
        self.target_c=target_c
        self.margin_c=margin_c
        self.stage=0
        self.risk_count=0

    def update(self, temperature, forecast):
        risk=max(temperature,forecast) if math.isfinite(forecast) else temperature
        boundaries=(self.target_c-1.,self.target_c-.5,self.target_c)
        while self.stage<3 and risk>=boundaries[self.stage]:
            self.stage+=1
        while self.stage>0 and risk<boundaries[self.stage-1]-self.margin_c:
            self.stage-=1
        names=('절전 유지','예방 냉각','상한 보호','상한 회복')
        limits=(self.target_c-1.,self.target_c-.5,self.target_c,self.target_c)
        return names[self.stage],limits[self.stage]

    def upper_risk(self, temperature, forecast):
        predicted=math.isfinite(forecast) and temperature>=self.target_c-.5 and forecast>=self.target_c
        self.risk_count=self.risk_count+1 if predicted else 0
        return temperature>=self.target_c or self.risk_count>=2


class SafeEnergySwitchGate:
    def __init__(self, ratio=.1, absolute=0., confirmations=5, hold_seconds=15.):
        self.ratio=ratio;self.absolute=absolute
        self.confirmations=confirmations;self.hold_seconds=hold_seconds
        self.reset()

    def reset(self):
        self.pending=None;self.count=0

    def decide(self,current,proposed,now,last_change,maintenance_seconds,upper_risk=False):
        if current is None:
            self.reset()
            return proposed,'초기 명령: 후보 선택'
        key=(proposed.intake_pwm,proposed.exhaust_pwm)
        if key==(current.intake_pwm,current.exhaust_pwm):
            self.reset()
            return current,'현재 조합 유지'
        improvement=(proposed.safe and not current.safe) or (math.isfinite(proposed.predicted_temp_c) and
                     (not math.isfinite(current.predicted_temp_c) or
                      proposed.predicted_temp_c<current.predicted_temp_c-.02))
        if upper_risk and improvement:
            self.reset()
            return proposed,'실제/연속 상한 위험: 예측 개선 후보로 대기 우회'
        if not current.safe or not proposed.safe:
            self.reset()
            return current,'상한 안전 미확인: 위험 확인 전 현재 명령 유지'
        # Missing recovery time is uncertainty, not evidence of immediate danger.
        if current.recovery_seconds is None or proposed.recovery_seconds is None:
            self.reset()
            return current,'회복시간 불확실: 최대 출력 전환 없이 현재 조합 유지'
        if current.recovery_seconds==0 and proposed.recovery_seconds==0:
            old=current.relative_energy*maintenance_seconds
            new=proposed.relative_energy*maintenance_seconds
        else:
            old=current.total_relative_energy;new=proposed.total_relative_energy
        if old is None or new is None or not math.isfinite(old) or not math.isfinite(new):
            self.reset()
            return current,'에너지 비교 불가: 유지'
        if old-new<=max(self.ratio*old,self.absolute):
            self.reset()
            return current,'예상 절감 기준 미달: 유지'
        self.count=self.count+1 if self.pending==key else 1
        self.pending=key
        if self.count<self.confirmations or now-last_change<self.hold_seconds:
            return current,f'경제성 확인 {self.count}/{self.confirmations}: 최소 유지시간 적용'
        self.reset()
        return proposed,'안전 조건 및 경제성 연속 확인 후 변경'
