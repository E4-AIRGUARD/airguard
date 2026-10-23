"""PM 감쇠 시간표 모델 (순수 파이썬, 외부 라이브러리 없음).

입력: E4_AIRGUARD 전처리 폴더들의 02_팬시작0초_회복분석/*.csv (PM은 후행3중앙값 + 상한 500 적용본)
출력: model.json
  - 구간 A (PM500 -> 50): 조합별 '현재 PM에서 PM50까지 남은 시간' 표. 반복 간 편차가 작아 조합별로 따로 쓴다.
  - 구간 B (PM50 -> 20): 조합과 무관하게 한 표로 합친 '남은 시간' 표. 반복 편차가 크고 조합 효과가 안 보인다.
  - 전력: RPM 기반 추정값(fan_power_rpm추정.csv, 5V x 0.27A x (RPM/7500)^3). 실측 아님.
"""
import csv, glob, json, math, os, statistics, sys

LEVELS = [500, 400, 300, 200, 150, 100, 75, 50, 40, 30, 25, 20]
A_END = 50      # 구간 A/B 경계
TARGET = 20


def load_power(path):
    w = {}
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            w[(r['fan'], int(r['PWM']))] = float(r['W'])
    return w


def combo_power(power, key):
    i, e = map(int, key.split(','))
    return power[('intake', i)] + power[('exhaust', e)]


def load_runs(roots):
    """각 팬가동이후 CSV 하나가 독립 실험 1회. 조합은 마지막 행 PWM(첫 행은 전환 중일 수 있음)."""
    runs = []
    for root in roots:
        for f in sorted(glob.glob(os.path.join(root, '02_팬시작0초_회복분석', '*.csv'))):
            with open(f, encoding='utf-8-sig') as fp:
                rows = list(csv.DictReader(fp))
            t = [float(r['Time[s]']) for r in rows]
            pm = [float(r['PM2.5[ug/m3]']) for r in rows]
            key = '%d,%d' % (round(float(rows[-1]['Intake_PWM[%]'])), round(float(rows[-1]['Exhaust_PWM[%]'])))
            if any(a >= b for a, b in zip(t, t[1:])):
                raise ValueError('time not increasing: ' + f)
            last500 = max(i for i, v in enumerate(pm) if v >= 500)
            cr = {}
            for L in LEVELS[1:]:
                j = next((i for i in range(last500, len(pm)) if pm[i] <= L), None)
                if j is None:
                    raise ValueError('level %d not reached: %s' % (L, f))
                cr[L] = t[j] - t[last500]
            runs.append({'id': os.path.basename(f).replace('__팬가동이후.csv', '').replace('SMOKE_RECOVERY_', ''),
                         'day': rows[0]['Measurement_Timestamp'][:10], 'combo': key,
                         'cross': cr, 'peak_to_last500_s': t[last500]})
    return runs


def _q(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def build(runs, power, tail_days=None):
    """tail_days: 구간 B 표에 쓸 측정일 목록(None이면 전부)."""
    levels_a = [L for L in LEVELS[1:] if L >= A_END]       # 400..50
    levels_b = [L for L in LEVELS if L <= A_END]           # 50..20
    model = {'levels_a': [500] + levels_a, 'levels_b': levels_b, 'target': TARGET,
             'power_source': 'RPM 기반 추정(5V x 0.27A x (RPM/7500)^3), 실측 아님', 'combos': {}}
    by = {}
    for r in runs:
        by.setdefault(r['combo'], []).append(r)
    for key, rs in sorted(by.items()):
        tg = {}   # level -> PM50까지 남은 시간 목록
        for L in [500] + levels_a:
            tg[L] = [r['cross'][A_END] - (0.0 if L == 500 else r['cross'][L]) for r in rs]
        model['combos'][key] = {
            'n': len(rs), 'power_w': combo_power(power, key),
            'to50_mean': {str(L): statistics.mean(v) for L, v in tg.items()},
            'to50_max': {str(L): max(v) for L, v in tg.items()},
            'to50_min': {str(L): min(v) for L, v in tg.items()}}
    tr = [r for r in runs if tail_days is None or r['day'] in tail_days]
    tail = {}
    for L in levels_b:
        v = [r['cross'][TARGET] - r['cross'][L] for r in tr]
        tail[str(L)] = {'median': statistics.median(v), 'p75': _q(v, 0.75), 'mean': statistics.mean(v)}
    model['tail'] = {'n_runs': len(tr), 'days': sorted({r['day'] for r in tr}), 'to20': tail}
    model['runs'] = [{'id': r['id'], 'day': r['day'], 'combo': r['combo']} for r in runs]
    return model


def _interp_log(levels, vals, pm):
    """PM(로그 축)에서 선형보간. levels는 내림차순, vals는 같은 순서."""
    if pm >= levels[0]:
        return vals[0]
    if pm <= levels[-1]:
        return vals[-1]
    for hi, lo, vh, vl in zip(levels, levels[1:], vals, vals[1:]):
        if lo <= pm <= hi:
            w = (math.log(pm) - math.log(lo)) / (math.log(hi) - math.log(lo))
            return vl + (vh - vl) * w


def to50(model, key, pm, mode='mean'):
    """조합 key로 현재 pm에서 PM50까지 남은 시간(초). pm<=50이면 0."""
    if pm <= A_END:
        return 0.0
    la = model['levels_a']
    t = model['combos'][key]['to50_' + mode]
    return _interp_log(la, [t[str(L)] for L in la], pm)


def to20_tail(model, pm, stat='median'):
    """PM<=50에서 PM20까지 남은 시간(초, 조합 무관). pm<=20이면 0."""
    if pm <= TARGET:
        return 0.0
    lb = model['levels_b']
    t = model['tail']['to20']
    return _interp_log(lb, [t[str(L)][stat] for L in lb], min(pm, A_END))


def predict_total(model, key, pm, stat='median'):
    """고정 조합 key를 끝까지 쓸 때 pm에서 PM20까지 예상 시간(구간 A + 구간 B)."""
    return to50(model, key, pm) + to20_tail(model, A_END, stat) if pm > A_END else to20_tail(model, pm, stat)


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', '팬제어_데이터_E4_AIRGUARD')
    roots = sorted(glob.glob(os.path.join(data, 'E4_AIRGUARD_*')))
    roots = [r for r in roots if os.path.isdir(r)]
    runs = load_runs(roots)
    power = load_power(os.path.join(here, 'fan_power_rpm추정.csv'))
    days = sorted({r['day'] for r in runs})
    model = build(runs, power, tail_days=[days[-1]])          # 뚜껑 구멍 뚫은 최신 방식만 구간 B에 사용
    out = os.path.join(here, 'model.json')
    json.dump(model, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('runs', len(runs), 'combos', len(model['combos']), 'tail days', model['tail']['days'], '->', out)
