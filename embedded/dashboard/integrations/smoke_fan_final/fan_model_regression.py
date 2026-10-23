"""회귀 시간 예측 모델 생성 (개발용, numpy 필요 / 젯슨에서는 만들어진 model.json만 쓰므로 필요 없음).

PM500->50 총 시간 T 를 팬 PWM으로 예측하는 회귀:
    1/T = k0 + k1*s(in) + k2*s(out) + k3*s(in)*s(out),  s(x) = x/(x+h)   (x = PWM/100, h는 포화 정도)
학습 데이터: 옛 20회(model_시간표_옛20회.json의 조합 평균 x 반복 수) + 새 CSV들(03_PM500_PM50_감소비교/PM500_PM50_도달시간.csv, 젯슨_A01_A09_도달시간.csv 등). 경로 뒤에 :가중치를 붙이면 그 CSV 행을 가중치 배로 반영(배치 환경 데이터를 더 중요하게).
전력(power_w)은 fan_power_rpm추정.csv(실측 RPM)에서 다시 계산한다.
구간별 모양(500->400->...->50에 걸리는 시간 비율)과 꼬리(PM50->20)는 옛 시간표의 것을 그대로 쓴다.
  python3 fan_model_regression.py <새 도달시간 CSV>[:가중치] ...
"""
import csv, json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fan_model import load_power, combo_power

HERE = os.path.dirname(os.path.abspath(__file__))
HS = [0.02 * k for k in range(5, 100)]      # h >= 0.1: 포화가 너무 가파르면 계수가 폭주한다


def feats(i, e, h):
    si, se = i / (i + h), e / (e + h)
    return [1.0, si, se, si * se]


def fit(rows):
    best = None
    for h in HS:
        A = np.array([feats(i, e, h) for i, e, t in rows])
        w = np.linalg.lstsq(A, np.array([1 / t for *_, t in rows]), rcond=None)[0]
        r = A @ w
        if np.any(r <= 0):
            continue
        err = float(np.sum((1 / r - np.array([t for *_, t in rows])) ** 2))
        if best is None or err < best[0]:
            best = (err, h, w)
    return best


def main(csvs):
    base = json.load(open(os.path.join(HERE, 'model_시간표_옛20회.json'), encoding='utf-8'))
    rows = []
    for c, v in base['combos'].items():
        i, e = map(int, c.split(','))
        rows += [(i / 100, e / 100, v['to50_mean']['500'])] * v['n']
    n_old = len(rows)
    for spec in csvs:
        p, _, wt = spec.partition(':')
        for r in csv.DictReader(open(p, encoding='utf-8-sig')):
            if r['PM500_to_PM50_s']:
                rows += [(float(r['Intake_PWM[%]']) / 100, float(r['Exhaust_PWM[%]']) / 100, float(r['PM500_to_PM50_s']))] * int(wt or 1)
    _, h, w = fit(rows)
    shape = {}
    for L in base['levels_a']:
        shape[L] = sum(v['to50_mean'][str(L)] / v['to50_mean']['500'] for v in base['combos'].values()) / len(base['combos'])
    out = json.loads(json.dumps(base))
    power = load_power(os.path.join(HERE, 'fan_power_rpm추정.csv'))
    for c, v in out['combos'].items():
        i, e = map(int, c.split(','))
        v['power_w'] = combo_power(power, c)
        T = 1 / float(np.array(feats(i / 100, e / 100, h)) @ w)
        v['to50_mean'] = {str(L): T * shape[L] for L in base['levels_a']}
        v['to50_max'] = dict(v['to50_mean']); v['to50_min'] = dict(v['to50_mean'])
        v['n'] = sum(1 for a, b, _ in rows if round(a * 100) == i and round(b * 100) == e)      # 가중치 반영 행 수
    out['power_source'] = 'RPM 기반 추정(5V x 0.27A x (RPM/7500)^3), RPM은 10/20 젯슨 텔레메트리 중앙값, 전력계 실측 아님'
    out['model_type'] = '회귀: 1/T = k0 + k1*s(in) + k2*s(out) + k3*s(in)*s(out), s(x)=x/(x+h)'
    out['regression'] = {'h': h, 'w': [float(x) for x in w], 'n_train': len(rows), 'n_old': n_old}
    json.dump(out, open(os.path.join(HERE, 'model.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('train', len(rows), 'h=%.2f' % h, 'w=', [round(float(x), 5) for x in w])
    for c, v in sorted(out['combos'].items()):
        print(c, 'T500->50 = %.1f s' % v['to50_mean']['500'])


if __name__ == '__main__':
    main(sys.argv[1:])
