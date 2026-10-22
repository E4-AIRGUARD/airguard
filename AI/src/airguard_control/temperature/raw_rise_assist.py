"""학습 모델과 독립적인 원본 온도 상승률 예방 냉각 보조입니다."""
from dataclasses import dataclass

from airguard_control.temperature.temperature_features import raw_temperature_rates


@dataclass(frozen=True)
class RiseObservation:
    active: bool
    triggered: bool
    rate_1s: float
    rate_3s: float
    rate_5s: float
    projected_temp_c: float


class RawRiseAssist:
    def __init__(self,target_c=26.,rate_threshold=.08,hold_seconds=3.):
        if rate_threshold<=0 or hold_seconds<0:
            raise ValueError('상승률은 양수, 유지시간은 0 이상이어야 합니다.')
        self.target_c=target_c; self.rate_threshold=rate_threshold; self.hold_seconds=hold_seconds
        self.active=False; self.until=-float('inf'); self.clears=0; self.last_timestamp=None
        self.last=RiseObservation(False,False,0.,0.,0.,float('nan'))

    def update(self,samples):
        if not samples: return self.last
        now=samples[-1].timestamp
        if self.last_timestamp is not None and now<=self.last_timestamp:
            return RiseObservation(self.active,False,self.last.rate_1s,self.last.rate_3s,self.last.rate_5s,self.last.projected_temp_c)
        if self.last_timestamp is not None and now-self.last_timestamp>2.5:
            self.active=False; self.clears=0
        self.last_timestamp=now
        values=[s.temperature_c for s in samples]; times=[s.timestamp for s in samples]
        r1,r3,r5=map(float,raw_temperature_rates(values,times)[-1])
        projected=float(values[-1]+5*max(r3,0.))
        trigger=(len(samples)>=4 and r1>=self.rate_threshold and r3>=.03
                 and r3*3>=.12 and values[-1]>=self.target_c-1.5 and projected>=self.target_c-.5)
        was_active=self.active
        if trigger:
            self.active=True; self.until=now+self.hold_seconds; self.clears=0
        elif self.active:
            self.clears=self.clears+1 if r1<.02 and r3<.02 else 0
            if now>=self.until and self.clears>=3:
                self.active=False; self.clears=0
        self.last=RiseObservation(self.active,self.active and not was_active,r1,r3,r5,projected)
        return self.last
