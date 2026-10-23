"""가상 데이터 폐루프 시험: 평소 PM 36에서 연기가 올라왔다가(최대 약 1200) 팬 제어로 내려오는 상황을 흉내 낸다.
  python3 test_virtual.py [마감초=70] [로그.csv]
팬 조합이 세질수록 PM이 빨리 내려가는 가상 플랜트(감쇠 속도는 model.json의 PM500->50 시간표에서 환산)를 쓰므로,
'제어기가 제때 올리고 내리는지'를 보는 시험이지 실제 성능 평가가 아니다."""
import json, math, os, random, sys
from fan_controller import Planner, FanController, EnergyMeter
from fan_model import to50

HERE = os.path.dirname(os.path.abspath(__file__))
M = json.load(open(os.path.join(HERE, 'model.json'), encoding='utf-8'))
BG, T_SMOKE, T_SRC = 36.0, 130.0, 26.0          # 배경 PM, 연기 시작, 연기 발생 지속 시간(초)
SRC = 62.0                                        # 연기 발생 세기(PM/s)


def k_of(combo):                                  # 조합별 감쇠율 = ln(10) / (PM500->50 시간)
    return math.log(10) / to50(M, combo, 500)


def episode(policy, deadline, seed=1, T_END=420.0, log=None, dropouts=((300.0, 301.0), (330.0, 340.0), (360.0, 375.0))):
    rnd = random.Random(seed)
    P = Planner(M, prune=False)
    ctl = FanController(P, deadline) if policy == 'model' else None
    meter = EnergyMeter(P, log)
    pm, t, combo, dt, period = BG, 0.0, '35,35', 0.5, 2.0
    next_s, hist, e_win = 0.0, [], 0.0
    while t < T_END:
        src = SRC if T_SMOKE <= t < T_SMOKE + T_SRC else 0.0
        pm += (src - k_of(combo) * (pm - BG)) * dt
        t += dt
        if T_SMOKE <= t < T_SMOKE + 170:
            e_win += P.w[combo] * dt          # 연기 시작 후 170초 동안의 추정 에너지(정책 간 공정 비교용)
        if t >= next_s:
            next_s += period
            lost = any(a <= t < b for a, b in dropouts)
            raw = None if lost else max(0.0, round(pm * (1 + rnd.gauss(0, 0.02)) + rnd.gauss(0, 1.0)))
            if policy == 'model':
                ctl.feed(t, raw); d = ctl.tick(t)
            else:
                from fan_controller import Decision
                d = Decision(t, raw if raw is not None else math.nan, policy, 'fixed', False)
            meter.update(d)
            combo = d.combo
            hist.append((t, pm, combo))
    meter.close()
    t_up = next((t for t, p, c in hist if t >= T_SMOKE and p > 50), None)
    t_react = next((t for t, p, c in hist if t >= T_SMOKE and c != '35,35'), None)
    t_back = next((t for t, p, c in hist if t > T_SMOKE + T_SRC and p <= 50), None)
    t_fan_down = next((t for t, p, c in hist if t > T_SMOKE + T_SRC and c == '35,35' and p < 80), None)
    peak = max(p for _, p, _ in hist)
    e_ep = None
    return {'정책': policy if policy != 'model' else 'model(마감 %s)' % deadline, '최고 PM': round(peak),
            '반응 지연(초)': None if policy != 'model' or t_react is None or t_up is None else round(t_react - t_up, 1),
            'PM50 아래 복귀(초, 연기 시작 후)': None if t_back is None else round(t_back - T_SMOKE),
            '연기 후 170초 에너지(J)': round(e_win, 1)}


if __name__ == '__main__':
    dl = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0
    log = sys.argv[2] if len(sys.argv) > 2 else None
    rows = [episode('model', dl, log=log), episode('model', None), episode('35,35', None), episode('100,100', None)]
    for r in rows:
        print(r)
