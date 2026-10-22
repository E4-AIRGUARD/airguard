"""검증 + 결과표 생성. 실행: python3 benchmarks/validate.py  (fan_model.py로 model.json을 만든 뒤)"""
import csv, glob, json, math, os, statistics as S, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, '..', 'src'))
from paths import MODELS, DATA, RESULTS
from fan_model import load_runs, load_power, build, to50, to20_tail, predict_total, A_END, TARGET
from fan_controller import Planner

data = os.path.join(DATA, 'raw')
runs = load_runs(sorted(d for d in glob.glob(os.path.join(data, 'E4_AIRGUARD_*')) if os.path.isdir(d)))
power = load_power(os.path.join(DATA, 'fan_power_rpm추정.csv'))
days = sorted({r['day'] for r in runs}); new_day = days[-1]


def actual(r, start=500):
    c = r['cross']
    a = c[A_END] - (0 if start == 500 else c[start])
    return a, c[TARGET] - c[A_END], c[TARGET] - (0 if start == 500 else c[start])


def mae(x): return S.mean(abs(v) for v in x)


# 1) 실험 1개씩 빼고 예측 (leave-one-run-out)
rows = []
for i, r in enumerate(runs):
    rest = [x for j, x in enumerate(runs) if j != i]
    tail_days = [new_day] if any(x['day'] == new_day for x in rest) else None
    m = build(rest, power, tail_days=tail_days)
    a, b, tot = actual(r)
    pa = to50(m, r['combo'], 500); pb = to20_tail(m, A_END, 'median')
    base_a = S.mean(actual(x)[0] for x in rest)
    rows.append({'실험': r['id'], '날짜': r['day'], '조합': r['combo'],
                 'A실측_500to50_s': round(a, 1), 'A예측_s': round(pa, 1), 'A기준선_전체평균_s': round(base_a, 1),
                 'B실측_50to20_s': round(b, 1), 'B예측_s': round(pb, 1),
                 '총실측_s': round(tot, 1), '총예측_s': round(pa + pb, 1)})
with open(os.path.join(RESULTS, '검증_실험1개씩빼기.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
eA = [x['A예측_s'] - x['A실측_500to50_s'] for x in rows]
eA0 = [x['A기준선_전체평균_s'] - x['A실측_500to50_s'] for x in rows]
eB = [x['B예측_s'] - x['B실측_50to20_s'] for x in rows]
eT = [x['총예측_s'] - x['총실측_s'] for x in rows]
eB_new = [x['B예측_s'] - x['B실측_50to20_s'] for x in rows if x['날짜'] == new_day]
res = {'LOO_B_MAE_s(신규 날짜 9회만)': mae(eB_new), 'LOO_A_MAE_s': mae(eA), 'LOO_A_기준선(전체평균)_MAE_s': mae(eA0), 'LOO_B_MAE_s': mae(eB), 'LOO_총_MAE_s': mae(eT)}

# 2) 날짜 간: 10/08만으로 만들고 10/15 예측
old = [r for r in runs if r['day'] != new_day]; new = [r for r in runs if r['day'] == new_day]
m_old = build(old, power, tail_days=[d for d in days if d != new_day])
xa, xb = [], []
for r in new:
    a, b, _ = actual(r)
    if r['combo'] in m_old['combos']:
        xa.append(to50(m_old, r['combo'], 500) - a)
    xb.append(to20_tail(m_old, A_END, 'median') - b)
res.update({'날짜간_A_MAE_s(10/08->10/15)': mae(xa), '날짜간_A_편향_s': S.mean(xa),
            '날짜간_B_MAE_s': mae(xb), '날짜간_B_편향_s(예측-실측, +면 구멍 전 방식이 더 느림)': S.mean(xb)})

# 3) 조합별 요약
mod = json.load(open(os.path.join(MODELS, 'model.json'), encoding='utf-8'))
P = Planner(mod)
summ = []
for c in P.all_keys:
    v = [actual(r)[0] for r in runs if r['combo'] == c]
    pw = mod['combos'][c]['power_w']
    summ.append({'조합(흡기,배기)': c, '횟수': len(v), 'A_500to50_평균_s': round(S.mean(v), 1),
                 'A_표준편차_s': round(S.pstdev(v), 1) if len(v) > 1 else '', '추정전력_W': round(pw, 3),
                 'A구간_추정에너지_J': round(pw * S.mean(v), 1)})
summ.sort(key=lambda x: x['A_500to50_평균_s'])
with open(os.path.join(RESULTS, '조합별_요약.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(summ[0])); w.writeheader(); w.writerows(summ)

# 4) 마감별 계획표(모델 안에서의 예측)과 고정 조합 비교
plans = []
for d in range(110, 221, 10):
    p = P.plan(d)
    seq = []
    for a, b, c in p['segments']:
        if not seq or seq[-1][2] != c: seq.append([a, b, c])
        else: seq[-1][1] = b
    plans.append({'마감_s(PM20까지, 꼬리 p75 가정)': d, '구간별 조합': ' / '.join('%d~%d:%s' % tuple(s) for s in seq),
                  '예상 소요 중앙값_s': round(p['time_total_median_s']), '예상 에너지_J': round(p['energy_j'], 1)})
for c in P.all_keys:
    f = P.fixed(c)
    plans.append({'마감_s(PM20까지, 꼬리 p75 가정)': '고정 ' + c, '구간별 조합': c,
                  '예상 소요 중앙값_s': round(f['time_total_median_s']), '예상 에너지_J': round(f['energy_j'], 1)})
with open(os.path.join(RESULTS, '마감별_계획표.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(plans[0])); w.writeheader(); w.writerows(plans)

# 5) 꼬리(PM50->20)가 조합 전력과 관련 있는지: 신규 9회 순위상관
tl = [(P.w[r['combo']], actual(r)[1]) for r in new]
def rank(x): s = sorted(x); return [s.index(v) + 1 for v in x]
rx, ry = rank([a for a, _ in tl]), rank([b for _, b in tl]); n = len(tl)
rho = 1 - 6 * sum((a - b) ** 2 for a, b in zip(rx, ry)) / (n * (n * n - 1))
res['꼬리B vs 전력 순위상관(신규 9회)'] = rho
res['꼬리B 신규 중앙값_s'] = S.median(b for _, b in tl); res['꼬리B 구방식 중앙값_s'] = S.median(actual(r)[1] for r in old)
json.dump(res, open(os.path.join(RESULTS, '검증요약.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
for k, v in res.items(): print('%-60s %.2f' % (k, v))
print(); [print(x) for x in summ]; print(); [print(p) for p in plans]


# 6) 제어기 폐루프 모의: 모델 표로 A 구간 PM을 1초씩 진행시키며 제어기를 돌리고, 꼬리 B는 신규 9회 실측을 하나씩 붙인다.
from fan_controller import FanController
def simulate_A(planner, deadline):
    mdl = planner.m; pm, t, e = 600.0, 0.0, 0.0
    ctl = FanController(planner, deadline); seq = []
    while pm > A_END and t < 600:
        ctl.feed(t, min(pm, 500)); d = ctl.tick(t); c = d.combo
        if not seq or seq[-1] != c: seq.append(c)
        target = to50(mdl, c, min(pm, 500)) - 1.0
        lo, hi = A_END, 500.0
        for _ in range(40):                      # to50(c, x) = target 인 x를 이분법으로 찾는다
            mid = (lo + hi) / 2
            if to50(mdl, c, mid) > target: hi = mid
            else: lo = mid
        pm = lo if target > 0 else A_END
        e += planner.w[c] * 1.0; t += 1.0
    return t, e, seq
tails = [actual(r)[1] for r in new]
sim = []
for d in (110, 120, 130, 140, 150, 160, 200):
    ta, ea, seq = simulate_A(P, d)
    hit = sum(ta + b <= d for b in tails)
    sim.append({'마감_s': d, 'A구간 소요_s': round(ta), 'A구간 사용 조합': ' > '.join(seq),
                '신규 9회 꼬리로 총시간': '%d~%d' % (ta + min(tails), ta + max(tails)), '마감 지킨 횟수(9회 중)': hit})
for fx in ('100,60', '60,35', '35,35'):
    ta = to50(mod, fx, 500); hit = sum(ta + b <= 150 for b in tails)
    sim.append({'마감_s': '고정 ' + fx + ' (마감 150 기준)', 'A구간 소요_s': round(ta), 'A구간 사용 조합': fx,
                '신규 9회 꼬리로 총시간': '%d~%d' % (ta + min(tails), ta + max(tails)), '마감 지킨 횟수(9회 중)': hit})
with open(os.path.join(RESULTS, '제어기_모의_마감달성.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(sim[0])); w.writeheader(); w.writerows(sim)
print(); [print(x) for x in sim]
