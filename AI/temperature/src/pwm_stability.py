"""LSTM 예측의 한두 표본 흔들림이 팬 명령 변경으로 바로 이어지는 것을 줄입니다."""
from __future__ import annotations


class PwmStabilityGuard:
    def __init__(self, hold_seconds=8.0, confirmations=3, forecast_confirmations=2, target_c=26.):
        self.hold_seconds=hold_seconds
        self.confirmations=confirmations
        self.forecast_confirmations=forecast_confirmations
        self.pending=None
        self.count=0
        self.last_change=None
        self.target_c=target_c

    def decide(self, current, proposed, temperature, rate, current_forecast, now):
        if self.last_change is None:
            self.last_change=now
        if temperature>=self.target_c or (temperature>=self.target_c-.5 and rate>=.15):
            self.pending=None; self.count=0
            if proposed!=current: self.last_change=now
            return proposed, '실제 온도/급상승 조건: 안정화 대기 우회'
        if proposed==current:
            self.pending=None; self.count=0
            return current, '현재 PWM 유지'
        self.count=self.count+1 if self.pending==proposed else 1
        self.pending=proposed
        predicted_risk=current_forecast>=self.target_c
        required=self.forecast_confirmations if predicted_risk else self.confirmations
        hold_satisfied=predicted_risk or now-self.last_change>=self.hold_seconds
        if self.count>=required and hold_satisfied:
            self.pending=None; self.count=0; self.last_change=now
            return proposed, 'LSTM 후보 연속 확인 후 변경'
        return current, f'LSTM PWM 안정화 대기 {self.count}/{required}'
