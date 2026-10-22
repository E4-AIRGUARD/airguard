import json, math, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from smoke_fan import SmokeFan, combine_max
from event_recorder import EventRecorder, QueueSink


def test_model_loads_and_steps():
    s = SmokeFan()
    cmd = s.step(30.0, now=0.0)                       # 범위 안(PM50 이하)이면 가장 약한 35/35
    assert (cmd.intake, cmd.exhaust) == (35, 35)
    cmd = s.step(900.0, now=1.0)                      # 500 초과는 가장 빠른 조합
    assert cmd.reason == 'pm_over_limit_fastest' or cmd.intake >= 35


def test_sensor_stale_goes_to_safe():
    s = SmokeFan()
    s.step(30.0, now=0.0)
    cmd = s.step(None, now=30.0)
    assert (cmd.intake, cmd.exhaust) == (100, 100)


def test_combine_max():
    class C: intake, exhaust = 35, 100
    assert combine_max(C, 60, 35) == (60, 100)


def test_event_lifecycle(tmp_path):
    db = str(tmp_path / 'p.sqlite3')
    r = EventRecorder(QueueSink(db), 'dev', category='pm25')
    done = None
    for t in range(40):
        pm = 30 if t < 5 else (200 if t < 20 else 30)
        done = r.update(1000 + t, pm, 35, 35) or done
    assert done and done['status'] == 'completed' and done['category'] == 'pm25'
    q = sqlite3.connect(db)
    assert q.execute("select count(*) from queue where kind='sample'").fetchone()[0] > 10
    assert json.loads(done['config_json'])['category'] == 'pm25'
