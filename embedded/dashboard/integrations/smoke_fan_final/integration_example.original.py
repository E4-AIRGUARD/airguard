"""연기 + 온도 통합 루프 예제 (센서·팬 함수는 팀 코드로 교체). python3 integration_example.py --fake 로 확인."""
import math, sys, time
from smoke_fan import SmokeFan, combine_max


def read_pm():            raise NotImplementedError   # 연기 센서
def read_temp_cmd():      raise NotImplementedError   # 온도 쪽 제어 결과 -> (intake_pct, exhaust_pct), 요구 없으면 (35, 35)
def set_fan(i, e):        raise NotImplementedError   # ESP32 PWM 출력


if '--fake' in sys.argv:
    t_start = time.time()
    read_pm = lambda: max(15.0, 800 * math.exp(-(time.time() - t_start) / 30) + 18)
    read_temp_cmd = lambda: (35, 35)
    set_fan = lambda i, e: None

smoke = SmokeFan(log_path=None)
last = None
try:
    while True:
        try:
            pm = read_pm()
        except Exception:
            pm = None                                  # 센서 오류는 None -> 10초 넘게 이어지면 연기 쪽이 100/100 요구
        s = smoke.step(pm)
        ti, te = read_temp_cmd()
        i, e = combine_max(s, ti, te)
        if (i, e) != last:
            print('PM=%s 연기요구=%d/%d 온도요구=%d/%d -> 출력 %d/%d (%s)' % (s.pm, s.intake, s.exhaust, ti, te, i, e, s.reason))
            set_fan(i, e); last = (i, e)
        time.sleep(1.0 if '--fake' not in sys.argv else 0.05)
except KeyboardInterrupt:
    pass
finally:
    set_fan(100, 100)
    print('연기 쪽 추정 에너지 %.1f J' % smoke.meter.energy_j)
