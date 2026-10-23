"""원본 온도에서 실제 시간 간격을 사용해 다중 변화율을 계산합니다."""
import numpy as np


def raw_temperature_rates(temperatures, timestamps, lags=(1,3,5)):
    values=np.asarray(temperatures,dtype=float)
    times=np.asarray(timestamps,dtype=float)
    result=np.zeros((len(times),len(lags)),dtype=np.float32)
    for i in range(len(times)):
        for k,lag in enumerate(lags):
            if i==0: continue
            j=int(np.argmin(np.abs(times[:i]-(times[i]-lag))))
            dt=times[i]-times[j]
            if abs(dt-lag)<=.6 and dt>0 and np.diff(times[j:i+1]).max(initial=0)<=2.5:
                result[i,k]=(values[i]-values[j])/dt
    return result
