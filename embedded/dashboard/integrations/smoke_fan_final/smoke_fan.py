"""연기(PM2.5) 팬 자동제어 통합용 모듈. 센서/팬 코드와 분리되어 있어 다른 제어(온도 등)와 한 루프에서 같이 쓸 수 있다.

사용 (메인 루프에서 1초마다):
    from smoke_fan import SmokeFan
    smoke = SmokeFan()                          # model.json, 마감 70초 (run_on_jetson.py 기본값과 같음)
    ...
    cmd = smoke.step(pm)                        # pm: PM2.5 원시값 또는 None(센서 오류)
    cmd.intake, cmd.exhaust                     # 연기 쪽이 요구하는 PWM(%). 35/60/100 중 하나
    cmd.reason                                  # 판단 이유(로그용)
    cmd.energy_j                                # 연기 쪽 추정 누적 에너지(J)

이 모듈은 팬을 직접 돌리지 않는다. 최종 PWM은 온도 쪽 결과와 합쳐서 호출하는 쪽이 정한다(combine_max 참고).
"""
import json, os, time
from dataclasses import dataclass

from fan_controller import Planner, FanController, EnergyMeter

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class FanCommand:
    intake: int
    exhaust: int
    reason: str
    pm: float
    energy_j: float


class SmokeFan:
    def __init__(self, model_path='model.json', deadline_s=70.0, range_pm=50.0, interval_s=1.0, log_path=None, event_db=None, device_id='jetson-1'):
        with open(os.path.join(HERE, model_path), encoding='utf-8') as model_file:
            model = json.load(model_file)
        self.planner = Planner(model, prune=False, include_tail=False)
        self.ctl = FanController(self.planner, deadline_s or None, interval_s=interval_s, range_pm=range_pm)
        self.meter = EnergyMeter(self.planner, log_path)
        self.t0 = None
        self.rec = None
        if event_db:     # PM 이벤트(50 초과 -> 50 이하 회복)를 로컬 대기열에 기록. 올리는 건 uploader.py
            from event_recorder import EventRecorder, QueueSink
            self.rec = EventRecorder(QueueSink(event_db), device_id, pm_limit=range_pm, config={'deadline_s': deadline_s, 'model': model_path, 'pwm_source': 'smoke_demand'})

    def step(self, pm, now=None, event_time=None):
        """now는 제어 시계, event_time은 Unix 시각(생략하면 time.time()). 기록 PWM은 PM 요구값."""
        now = time.time() if now is None else now
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        self.ctl.feed(t, pm)
        d = self.ctl.tick(t)
        row = self.meter.update(d)
        i, e = (int(x) for x in d.combo.split(','))
        if self.rec:
            # The control clock may be monotonic; event timestamps must be Unix time.
            self.rec.update(time.time() if event_time is None else event_time, pm, i, e)
        return FanCommand(i, e, d.reason, d.pm, self.meter.energy_j)

    def close(self):
        self.meter.close()
        if self.rec:
            self.rec.close()
            self.rec.sink.close()


def combine_max(smoke_cmd, temp_intake, temp_exhaust):
    """두 제어의 요구 중 흡기·배기 각각 더 센 쪽을 쓴다(제안 규칙: 한쪽이 환기를 요구하면 약하게 낮추지 않음).
    온도 쪽 결과가 PWM(%)이면 35/60/100 중 가까운 값으로 맞춰서 넣으면 된다."""
    return max(smoke_cmd.intake, temp_intake), max(smoke_cmd.exhaust, temp_exhaust)
