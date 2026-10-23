"""Frozen v6 preprocessing, extracted unchanged from the development implementation."""
import numpy as np
from sensor_buffer import SensorBuffer, SensorSample

def examples(frame, future_required=True, include_raw_rates=False):
    """현재 런타임과 같은 8행 버퍼/중앙값/시간 기반 변화율을 적용합니다."""
    output=[]
    for run,part in frame.groupby('run',sort=False):
        part=part.sort_values('timestamp').reset_index(drop=True)
        times=part.timestamp.to_numpy(float)
        buffer=SensorBuffer(window_size=8)
        for index,row in enumerate(part.itertuples()):
            if index and times[index]-times[index-1]>2.5:
                buffer=SensorBuffer(window_size=8)
            buffer.append(SensorSample(timestamp=row.timestamp,temperature_c=row.Temp,
                                       intake_pwm=row.Intake_PWM,exhaust_pwm=row.Exhaust_PWM))
            if not buffer.is_ready(): continue
            history=buffer.control_feature_matrix(row.Intake_PWM,row.Exhaust_PWM,include_raw_rates=include_raw_rates)
            insertion=int(np.searchsorted(times,row.timestamp+5))
            candidates=[j for j in (insertion-1,insertion) if index<j<len(times)]
            if not candidates: continue
            future=min(candidates,key=lambda j:abs(times[j]-row.timestamp-5))
            if future>=len(part) or abs(times[future]-row.timestamp-5)>.6: continue
            segment=part.iloc[index:future+1]
            if segment.timestamp.diff().dropna().gt(2.5).any(): continue
            held=bool(segment.Intake_PWM.eq(row.Intake_PWM).all() and segment.Exhaust_PWM.eq(row.Exhaust_PWM).all())
            if future_required and not held: continue
            output.append(dict(run=run,timestamp=row.timestamp,history=history,
                               temp=float(history[-1,0]),raw_temp=row.Temp,
                               intake=row.Intake_PWM,exhaust=row.Exhaust_PWM,
                               target=float(part.Temp.iloc[future]),held=held))
    return output

