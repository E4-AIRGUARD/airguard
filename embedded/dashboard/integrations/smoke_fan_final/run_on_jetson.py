"""젯슨 실시간 테스트용 실행 스크립트.
  python3 run_on_jetson.py                 # dry-run(기본): 팬은 건드리지 않고 판단과 로그만 남김
  python3 run_on_jetson.py --live          # 실제 PWM 출력(set_fan을 채운 뒤에만)
  python3 run_on_jetson.py --fake          # 센서 없이 가짜 PM으로 루프 확인
  옵션: --model model_시간표_옛20회.json (통계 시간표로 실행, 기본은 회귀 model.json)  --deadline 70  (PM이 500 아래로 내려온 뒤 PM50 아래까지 마감 초, 기본 70. 500 위에서는 항상 가장 빠른 조합. 0이면 에너지 최소=항상 35/35)  --log energy_log.csv  --period 1.0
센서/PWM 연결부는 아래 read_pm, set_fan 두 함수만 채우면 된다."""
import argparse, json, math, os, time

from fan_controller import Planner, FanController, EnergyMeter

HERE = os.path.dirname(os.path.abspath(__file__))


def read_pm():
    """먼지센서에서 PM2.5 원시값(ug/m3)을 읽어 돌려준다. 읽기 실패면 None."""
    raise NotImplementedError('센서 읽는 코드를 여기에 넣으세요')


def set_fan(intake_pct, exhaust_pct):
    """흡기/배기 팬 PWM 듀티(%)를 출력한다."""
    raise NotImplementedError('PWM 출력 코드를 여기에 넣으세요')


def fake_pm_factory():
    t0 = time.time()
    return lambda: max(15.0, 500 * math.exp(-(time.time() - t0) / 40) + 18)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--fake', action='store_true')
    ap.add_argument('--deadline', type=float, default=70.0, help='PM이 500 아래로 내려온 뒤 PM50 아래로 내려올 때까지 허용 시간(초). 0이면 마감 없음(항상 35/35)')
    ap.add_argument('--log', default='energy_log.csv')
    ap.add_argument('--model', default='model.json', help='model.json(회귀, 기본) 또는 model_시간표_옛20회.json(통계 시간표)')
    ap.add_argument('--range-pm', type=float, default=50.0, help='PM이 이 값 이하면 범위 안으로 보고 35/35 유지(방 배경 농도가 50보다 높으면 올리세요)')
    ap.add_argument('--include-tail', action='store_true', help='마감에서 PM50->20 꼬리 시간을 미리 뺌(기본은 PM50 도달까지만)')
    ap.add_argument('--interval', type=float, default=1.0, help='제어기 재계획 주기(초)')
    ap.add_argument('--prune', action='store_true', help='후보를 3단계로 줄임(기본은 9개 조합 전부 비교)')
    ap.add_argument('--period', type=float, default=1.0, help='센서 읽는 주기(초)')
    ap.add_argument('--event-db', default=None, help='PM 이벤트(50 초과 -> 회복)를 쌓을 로컬 대기열 파일(예: pending.sqlite3). 지정하면 uploader.py가 DB로 올림')
    ap.add_argument('--device-id', default='jetson-1')
    a = ap.parse_args()

    with open(os.path.join(HERE, a.model), encoding='utf-8') as model_file:
        model = json.load(model_file)
    planner = Planner(model, prune=a.prune, include_tail=a.include_tail)
    ctl, meter = FanController(planner, a.deadline or None, interval_s=a.interval, range_pm=a.range_pm), EnergyMeter(planner, a.log)
    rec = None
    if a.event_db:
        from event_recorder import EventRecorder, QueueSink
        rec = EventRecorder(QueueSink(a.event_db), a.device_id, pm_limit=a.range_pm, config={'deadline_s': a.deadline, 'model': a.model, 'pwm_source': 'smoke_demand', 'live': a.live, 'fake': a.fake})
    reader = fake_pm_factory() if a.fake else read_pm
    last, t0 = None, time.time()
    try:
        while True:
            now = time.time() - t0           # 로그 시각은 시작 기준 초
            try:
                pm = reader()
            except Exception as e:           # 센서 오류는 None으로 넘기면 제어기가 안전 조합(100/100)으로 간다
                print('센서 오류:', e)
                pm = None
            ctl.feed(now, pm)
            d = ctl.tick(now)
            row = meter.update(d)
            if rec:
                ci, ce = (int(x) for x in d.combo.split(','))
                rec.update(time.time(), pm, ci, ce)
            if d.combo != last:
                i, e = (int(x) for x in d.combo.split(','))
                print('PM=%s -> 흡기/배기 %s (%s)' % (row['pm'], d.combo, d.reason))
                if a.live:
                    set_fan(i, e)
                last = d.combo
            time.sleep(a.period)
    except KeyboardInterrupt:
        pass
    finally:
        meter.close()
        if rec:
            rec.close()
            rec.sink.close()
        if a.live:
            set_fan(100, 100)                # 종료 시 안전하게 환기
        print('추정 누적 에너지 %.1f J' % meter.energy_j)


if __name__ == '__main__':
    main()
