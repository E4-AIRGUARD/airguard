"""더미 환경 시험: 모델 표와 다른 방식(지수 감쇠 + 배경농도 + 센서 잡음/튐/끊김)으로 만든 가짜 실험실에서 제어기를 돌린다.
확인하는 것: 제어기 로직·안전동작·로그가 실제 루프에서 문제없는가, 자동제어가 고정 조합 대비 에너지/시간에서 어디에 놓이는가.
확인하지 못하는 것: 실제 방에서의 정확도(더미는 우리가 만든 가정이므로). 그건 새 실험 표본으로만 알 수 있다."""
import json, math, os, random, statistics as S, sys, csv
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from paths import MODELS, RESULTS
from fan_model import to50
from fan_controller import Planner, FanController, EnergyMeter, SAFE

model = json.load(open(os.path.join(MODELS, 'model.json'), encoding='utf-8'))
P = Planner(model)
# 더미 플랜트: dPM/dt = -k (PM - bg). k는 조합별 PM500->50 시간에서 거꾸로 잡되 실험마다 +-15% 흔든다.
K = {c: math.log((500 - 20) / (50 - 20)) / to50(model, c, 500) for c in P.all_keys}


def episode(policy, seed, deadline=None, faults=False, log_path=None):
    rnd = random.Random(seed)
    bg = rnd.uniform(17, 21); kscale = rnd.uniform(0.85, 1.15)
    pm, t, hold = 650.0, 0.0, 0
    ctl = FanController(P, deadline) if policy == 'auto' else None
    meter = EnergyMeter(P, log_path)
    prev, switches, safe_hits, drop_until = None, 0, 0, -1
    while t < 500:
        if faults and 30 < t < 60 and drop_until < 0 and rnd.random() < 0.05:
            drop_until = t + 15                        # 15초 센서 끊김
        raw = pm + rnd.gauss(0, 1.5)
        if rnd.random() < 0.01: raw *= rnd.choice([0.4, 2.5])          # 튀는 값
        raw = max(0.0, round(raw))
        dropped = t < drop_until
        if policy == 'auto':
            ctl.feed(t, None if dropped else raw)
            d = ctl.tick(t); combo = d.combo
            if d.reason == 'sensor_invalid_or_stale': safe_hits += 1
            meter.update(d)
        else:
            combo = policy
            class _D: pass
            dd = _D(); dd.time_s, dd.pm, dd.combo, dd.reason = t, raw, combo, 'fixed'
            meter.update(dd)
        if prev is not None and combo != prev: switches += 1
        prev = combo
        pm += -K[combo] * kscale * (pm - bg) * 1.0 + rnd.gauss(0, 0.3)
        pm = max(pm, 0)
        if not dropped and min(raw, 500) <= 20: hold += 1
        elif not dropped: hold = 0
        t += 1.0
        if hold >= 3: break
    meter.close()
    return {'time': t, 'energy': meter.energy_j, 'done': hold >= 3, 'switches': switches, 'safe_hits': safe_hits}


def q(x, p): x = sorted(x); return x[min(len(x) - 1, int(p * len(x)))]


rows, N = [], 300
cases = [('고정 100,100', '100,100', None), ('고정 100,60', '100,60', None), ('고정 35,35', '35,35', None)]
cases += [('자동 마감%d' % d, 'auto', d) for d in (130, 150, 170)]
for name, pol, d in cases:
    eps = [episode(pol, s, d) for s in range(N)]
    done = [e for e in eps if e['done']]
    row = {'정책': name, '도달 비율': round(len(done) / N, 3), '시간 중앙값_s': round(S.median(e['time'] for e in done)),
           '시간 p90_s': round(q([e['time'] for e in done], 0.9)), '에너지 중앙값_J': round(S.median(e['energy'] for e in done), 1),
           '조합 전환 평균': round(S.mean(e['switches'] for e in eps), 1)}
    if d: row['마감 지킨 비율'] = round(sum(e['time'] <= d for e in done) / N, 3)
    rows.append(row)
fe = [episode('auto', 1000 + s, 150, faults=True) for s in range(100)]
fault = {'센서 끊김 포함 100회 중 안전(100,100) 전환이 있었던 횟수': sum(e['safe_hits'] > 0 for e in fe),
         '그래도 도달한 비율': sum(e['done'] for e in fe) / 100}
fields = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
with open(os.path.join(RESULTS, '더미시험_결과.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
for r in rows: print(r)
print(fault)
