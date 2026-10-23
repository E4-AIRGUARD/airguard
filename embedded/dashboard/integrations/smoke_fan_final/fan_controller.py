"""PM20 도달까지 추정 에너지가 가장 작은 팬 조합을 고르고, 구간마다 조합을 바꿀 수 있는 제어기.

원리
  1) PM 구간을 500->400->...->50 으로 나눈다. 조합 c가 구간 k를 지나는 시간 dt[c][k]는 model.json 표에서 나온다.
  2) 구간별 에너지 = 추정전력(W) x dt. 남은 시간 한도(마감) 안에서 에너지 합이 최소가 되도록 구간마다 조합을 고른다(동적계획법).
  3) PM50 이하는 조합 효과가 데이터에 안 보이므로 가장 약한 35,35를 쓴다.
  마감을 안 주면(deadline=None) 전 구간 35,35가 최소 에너지다. 마감이 짧을수록 앞 구간(고농도)부터 센 조합을 쓴다.

주의: 에너지는 RPM 기반 추정, 조합 전환 후 감쇠는 '현재 PM만 같으면 같은 속도'라는 가정이다(고정 조합 실험만 있어 전환 실험은 아직 없음).
"""
import csv, json, math, os, statistics, sys
from collections import deque
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fan_model import A_END, TARGET, to50, to20_tail

UNIT = 0.5          # 동적계획법 시간 눈금(초)
SAFE = '100,100'    # 센서 이상일 때 안전 측(가장 강한 환기)
LOW = '35,35'


class Planner:
    def __init__(self, model, tail_stat='p75', prune=True, include_tail=False):
        self.m, self.tail_stat = model, tail_stat
        self.keys = sorted(model['combos'])
        self.L = model['levels_a']                       # [500,400,...,50]
        self.K = len(self.L) - 1
        # 조합별 구간 시간은 표 차이를 그대로 쓰면 1~2초 잡음 때문에 비슷한 조합 사이를 구간마다 오가므로,
        # '구간 비율(조합 평균)' x '조합별 PM500->50 총시간'으로 매끈하게 만든다. (조합 간 비율 차이는 작음)
        total = {c: to50(model, c, self.L[0]) for c in self.keys}
        frac = [statistics.mean((to50(model, c, self.L[k]) - to50(model, c, self.L[k + 1])) / total[c] for c in self.keys)
                for k in range(self.K)]
        self.dt = {c: [total[c] * frac[k] for k in range(self.K)] for c in self.keys}
        self.all_keys = list(self.keys)
        self.w = {c: model['combos'][c]['power_w'] for c in self.keys}
        if prune:
            self.keys = self._tiers(total)   # 후보를 '시간-에너지 하한 곡선'의 뚜렷이 다른 단계로 줄인다(prune=False면 9개 전부 비교)
        # 마감은 기본적으로 'PM50 도달까지'. include_tail=True면 PM50->20 꼬리 시간(p75)을 마감에서 미리 뺀다.
        # (배경 PM이 20보다 높은 환경에서는 꼬리를 빼면 마감이 항상 빡빡해져 늘 가장 빠른 조합으로 쏠린다)
        self.tail_s = to20_tail(model, A_END, tail_stat) if include_tail else 0.0
        self._dp = {}

    def _tiers(self, total, merge_s=3.0):
        """PM500->50 총시간 T와 에너지 E=전력xT 평면에서 아래쪽 볼록 껍질에 있는 조합만 남기고,
        T 차이가 merge_s(표본 잡음 수준) 이내인 이웃은 에너지가 낮은 쪽만 둔다. 나머지는 더 느리거나 더 비싼 조합."""
        pts = sorted(((total[c], self.w[c] * total[c], c) for c in self.all_keys))   # T 오름차순
        hull = []
        for p in pts:
            while len(hull) >= 2 and (hull[-1][0] - hull[-2][0]) * (p[1] - hull[-2][1]) - (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0]) <= 0:
                hull.pop()
            hull.append(p)
        hull = hull[:hull.index(min(hull, key=lambda q: q[1])) + 1]     # 시간이 늘어도 에너지가 안 줄어드는 쪽은 제외
        out = []
        for h in hull:                                    # T 오름차순(빠른 쪽부터)
            if out and h[0] - out[-1][0] < merge_s:
                if h[1] < out[-1][1]:
                    out[-1] = h
                continue
            out.append(h)
        return [h[2] for h in out]

    def _solve(self, max_units):
        if max_units in self._dp:
            return self._dp[max_units]
        INF = float('inf')
        f = [[INF] * (max_units + 1) for _ in range(self.K + 1)]
        pick = [[None] * (max_units + 1) for _ in range(self.K)]
        f[self.K] = [0.0] * (max_units + 1)
        for k in range(self.K - 1, -1, -1):
            for b in range(max_units + 1):
                for c in self.keys:
                    u = math.ceil(self.dt[c][k] / UNIT)
                    if u <= b and f[k + 1][b - u] + self.w[c] * self.dt[c][k] < f[k][b]:
                        f[k][b] = f[k + 1][b - u] + self.w[c] * self.dt[c][k]
                        pick[k][b] = c
        self._dp[max_units] = (f, pick)
        return f, pick

    def segment(self, pm):
        """pm가 속한 구간 번호. PM>=500은 0, PM<=50은 None(꼬리 구간)."""
        if pm <= A_END:
            return None
        for k in range(self.K):
            if self.L[k + 1] < pm <= self.L[k] or (k == 0 and pm > self.L[0]):
                return k

    def choose(self, pm, remaining_s=None):
        """pm와 PM20까지 허용 남은 시간(remaining_s, None이면 무제한) -> (조합, 이유, 마감 달성 가능 여부)."""
        k = self.segment(pm)
        if k is None:
            return LOW, 'tail_lowest_power', None
        if remaining_s is None:
            return LOW, 'no_deadline_min_energy', None
        budget = remaining_s - self.tail_s
        if budget <= 0:
            return self._fastest(k), 'deadline_unattainable_fastest', False
        mx = int(budget / UNIT)
        f, pick = self._solve(min(mx, 1200))
        b = min(mx, 1200)
        if pick[k][b] is None:
            return self._fastest(k), 'deadline_unattainable_fastest', False
        return pick[k][b], 'min_energy_within_deadline', True

    def _fastest(self, k):
        """마감을 못 지킬 때: 가장 빠른 조합. 표본 잡음(약 2초) 안에서 비슷하게 빠르면 전력이 낮은 쪽."""
        tot = {c: sum(self.dt[c]) for c in self.keys}
        best = min(tot.values())
        return min((c for c in self.keys if tot[c] <= best + 2.0), key=lambda c: self.w[c])

    def plan(self, deadline_s):
        """PM500에서 시작해 마감(deadline_s, PM20 도달까지 총 시간) 안에 끝내는 최소에너지 계획."""
        rem, t, e, rows = deadline_s, 0.0, 0.0, []
        for k in range(self.K):
            c, why, ok = self.choose(self.L[k], rem)
            rows.append((self.L[k], self.L[k + 1], c))
            t += self.dt[c][k]; e += self.w[c] * self.dt[c][k]; rem -= self.dt[c][k]
        t_tail = to20_tail(self.m, A_END, 'median')
        e_tail = self.w[LOW] * t_tail
        return {'segments': rows, 'time_a_s': t, 'time_total_median_s': t + t_tail,
                'energy_j': e + e_tail, 'tail_median_s': t_tail}

    def fixed(self, c):
        t = to50(self.m, c, 500) + to20_tail(self.m, A_END, 'median')
        return {'time_total_median_s': t, 'energy_j': self.w[c] * to50(self.m, c, 500) + self.w[LOW] * to20_tail(self.m, A_END, 'median')}


@dataclass
class Decision:
    time_s: float
    pm: float
    combo: str
    reason: str
    changed: bool
    feasible: bool | None = None


class FanController:
    def __init__(self, planner, deadline_s=None, interval_s=5.0, min_hold_s=25.0, stale_after_s=10.0, rise_hold_s=6.0, rise_win_s=10.0, range_pm=A_END, stall_pm=80.0, stall_win_s=20.0, stall_drop=0.05):
        self.range_pm = range_pm      # 이 값 이하면 '범위 안'으로 보고 가장 약한 35,35로 둔다(방 배경 농도가 50보다 높을 때 올려서 쓴다)
        self.p, self.deadline_s = planner, deadline_s
        self.interval_s, self.min_hold_s, self.stale_after_s, self.rise_hold_s, self.rise_win_s = interval_s, min_hold_s, stale_after_s, rise_hold_s, rise_win_s
        self.buf = deque(maxlen=3)
        self.raw = deque(maxlen=3)   # 상한(500)을 씌우기 전 값. 측정 상한을 넘는 급상승을 알아보는 용도
        self.last_pm = 0.0
        self.hist = deque()          # 최근 rise_win_s초의 (시각, PM). 오르는 중인지 보려는 용도
        self.rise_t = -math.inf      # 마지막으로 PM이 오르는 걸 본 시각
        self.t0 = None
        self.cur, self.since = None, -math.inf
        self.last_eval = -math.inf
        self.last_sample_t = None
        self.done_since = None
        self.stall_pm, self.stall_win_s, self.stall_drop = stall_pm, stall_win_s, stall_drop   # PM이 stall_pm 이하에서 stall_win_s초 동안 stall_drop 비율도 안 내려가면 '정체'(팬을 세게 돌려도 소용없는 바닥값 근처)
        self.sbuf = deque()
        self.rate_min, self.rate_max = 0.7, 1.6   # 실시간 보정 비율의 허용 범위
        self.pred_acc = self.act_acc = 0.0        # 지금까지 모델이 예상한 하강 시간 / 실제 걸린 시간(PM75 초과 구간만)
        self.prev = None                          # 직전 틱의 (시각, PM, 조합)
        self.rate = 1.0                           # 실제/예상 시간 비율. 1보다 크면 모델보다 느리게 내려가는 중
        self.ep_t0 = None          # 회복 구간 시작 시각: PM이 A_END를 넘은 시점(상시 가동에서 마감을 여기서부터 잰다)

    def feed(self, t, raw_pm):
        """센서 표본 입력(시각 t[초], 원시 PM). 후행 3표본 중앙값을 쓴다(전처리와 같은 방식)."""
        if raw_pm is None or not math.isfinite(raw_pm) or raw_pm < 0:
            return                 # 한 번 빠진 값은 무시한다. stale_after_s 넘게 이어지면 tick()이 안전 조합으로 보낸다
        self.buf.append(min(raw_pm, 500.0))
        self.raw.append(min(raw_pm, 5000.0))
        self.last_sample_t = t
        if self.t0 is None:
            self.t0 = t

    def tick(self, now):
        """제어 주기마다 호출. 바뀌었든 아니든 현재 결정을 돌려준다."""
        if self.last_sample_t is None or now - self.last_sample_t > self.stale_after_s or not self.buf:
            return self._emit(now, math.nan, SAFE, 'sensor_invalid_or_stale', None, force=True)
        pm = statistics.median(self.buf)
        shown = statistics.median(self.raw)      # 로그/대시보드에는 500으로 자르기 전 값을 남긴다(판단에는 pm을 쓴다)
        self.hist.append((now, pm))
        while now - self.hist[0][0] > self.rise_win_s:
            self.hist.popleft()
        low = min(q for _, q in self.hist)
        if pm > A_END and pm >= 1.3 * low and pm - low >= 15:      # 잡음이 아니라 뚜렷한 상승
            self.rise_t = now
        rising = now - self.rise_t <= self.rise_hold_s       # 아직 오르는 중이면 감쇠 계획을 세우지 않고, 주기도 기다리지 않는다
        if now - self.last_eval < self.interval_s and self.cur is not None and not rising:
            return Decision(now, shown, self.cur, 'hold_interval', False)
        self.last_pm = pm
        if pm <= TARGET:
            return self._emit(now, shown, LOW, 'target_reached', None, force=True)
        if pm > A_END and self.ep_t0 is None and not rising and statistics.median(self.raw) < 500:
            self.ep_t0 = now       # 마감 시계는 '500 미만으로 내려온 뒤'부터 잰다(팬을 500에서 켜도 연기가 더 올라가는 구간은 예측 밖)
        elif pm <= A_END - 5:
            self.ep_t0 = None
        elapsed = 0.0 if self.ep_t0 is None else now - self.ep_t0
        rem = None if self.deadline_s is None else self.deadline_s - elapsed
        self._update_rate(now, pm)
        if rem is not None:
            rem = rem / self.rate     # 모델보다 느리게 내려가면 같은 계획에 더 많은 시간이 필요하다고 보고 남은 시간을 줄여 잡는다
        if pm <= self.range_pm and not rising:
            return self._emit(now, shown, LOW, 'in_range_lowest_power', None, force=True)
        stalled = self._stalled(now, pm)
        c, why, ok = self.p.choose(pm, rem)
        if stalled and not rising:
            c, why, ok = LOW, 'stalled_lowest_power', None      # 세게 돌려도 안 내려가는 구간이라 에너지만 아낀다
        urgent = False
        if rising:
            c, why, ok, urgent = self.p._fastest(0), 'pm_rising_fastest', None, True
        elif statistics.median(self.raw) > 500:      # 측정 상한 초과: 실제 농도를 모르므로 마감 계산 대신 가장 빠른 조합
            c, why, ok, urgent = self.p._fastest(0), 'pm_over_limit_fastest', None, True
        return self._emit(now, shown, c, why, ok, urgent=urgent)

    def _stalled(self, now, pm):
        """최근 stall_win_s초 동안 PM이 거의 안 내려가고, 값이 stall_pm 이하(그리고 범위 밖)면 True."""
        self.sbuf.append((now, pm))
        while self.sbuf and now - self.sbuf[0][0] > self.stall_win_s:
            self.sbuf.popleft()
        if pm > self.stall_pm or pm <= self.range_pm or now - self.sbuf[0][0] < self.stall_win_s - 2.0:
            return False
        first = self.sbuf[0][1]
        return first - pm < self.stall_drop * first

    def _update_rate(self, now, pm):
        """매 틱마다 '쓰고 있던 조합으로 모델이 예상한 하강 시간'과 '실제 걸린 시간'을 누적해 비율을 다시 계산한다.
        500 아래로 내려온 뒤(ep_t0)부터, PM75 초과 구간만 쓴다(PM50 근처는 잡음이 커서 제외)."""
        if self.ep_t0 is None:
            self.pred_acc = self.act_acc = 0.0; self.rate = 1.0; self.prev = None
            return
        if self.prev is not None:
            t0, pm0, c0 = self.prev
            if c0 in self.p.w and pm < pm0 and pm > 75:
                self.pred_acc += max(0.0, to50(self.p.m, c0, pm0) - to50(self.p.m, c0, pm))
                self.act_acc += now - t0
        self.prev = (now, pm, self.cur)
        if self.pred_acc > 3.0 and self.act_acc >= 10.0:
            self.rate = min(self.rate_max, max(self.rate_min, self.act_acc / self.pred_acc))

    def _emit(self, now, pm, c, why, ok, force=False, urgent=False):
        self.last_eval = now
        stronger = urgent and self.cur in self.p.w and self.p.w.get(c, 0.0) > self.p.w[self.cur]      # 급상승/측정상한 초과로 세게 올릴 때만 유지시간을 기다리지 않는다
        if c != self.cur and not force and not stronger and now - self.since < self.min_hold_s:
            return Decision(now, pm, self.cur, 'min_hold', False, ok)
        ch = c != self.cur
        if ch:
            self.cur, self.since = c, now
        return Decision(now, pm, c, why, ch, ok)


class EnergyMeter:
    """지시된 조합의 추정전력(W)을 시간에 곱해 누적 에너지(J)를 계산하고 한 줄씩 로그로 남긴다(대시보드 입력용).
    로그 열: time_s, pm, combo, power_w, energy_j(누적), reason. 팬 정지 구간은 combo=None, power_w=0.
    실제 PWM이 아니라 '지시한 조합' 기준의 추정치다(전력은 RPM 기반 추정)."""
    FIELDS = ['time_s', 'pm', 'combo', 'power_w', 'energy_j', 'reason']

    def __init__(self, planner, log_path=None):
        self.w, self.energy_j, self.last_t, self.last_combo = planner.w, 0.0, None, None
        self.f = open(log_path, 'w', newline='', encoding='utf-8-sig') if log_path else None
        self.writer = csv.DictWriter(self.f, fieldnames=self.FIELDS) if self.f else None
        if self.writer:
            self.writer.writeheader()

    def update(self, d):
        """FanController.tick()가 돌려준 Decision을 넣는다. 직전 조합의 전력 x 경과시간을 더한다."""
        if self.last_t is not None and self.last_combo in self.w:
            self.energy_j += self.w[self.last_combo] * max(0.0, d.time_s - self.last_t)
        self.last_t, self.last_combo = d.time_s, d.combo
        row = {'time_s': round(d.time_s, 3), 'pm': '' if d.pm != d.pm else round(d.pm, 1), 'combo': d.combo,
               'power_w': round(self.w.get(d.combo, 0.0), 4), 'energy_j': round(self.energy_j, 3), 'reason': d.reason}
        if self.writer:
            self.writer.writerow(row); self.f.flush()
        return row

    def close(self):
        if self.f:
            self.f.close()


def replay(planner, csv_path, deadline_s=None, log_path=None):
    """기록 CSV(팬가동이후)의 PM을 순서대로 넣어 '제어기가 어떤 조합을 지시했을지'만 출력. 실제 팬은 고정이었음."""
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8-sig')))
    ctl, meter = FanController(planner, deadline_s), EnergyMeter(planner, log_path)
    last, out = None, []
    for r in rows:
        t, pm = float(r['Time[s]']), float(r['PM2.5[ug/m3]'])
        ctl.feed(t, pm)
        d = ctl.tick(t)
        meter.update(d)
        if d.combo != last:
            out.append(d); last = d.combo
    meter.close()
    return out, meter.energy_j


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    model = json.load(open(os.path.join(here, 'model.json'), encoding='utf-8'))
    P = Planner(model)
    if len(sys.argv) >= 2 and sys.argv[1].endswith('.csv'):
        dl = float(sys.argv[2]) if len(sys.argv) > 2 else None
        log = sys.argv[3] if len(sys.argv) > 3 else None       # 에너지 로그 CSV 경로(선택)
        out, e = replay(P, sys.argv[1], dl, log)
        for d in out:
            print('t=%6.1fs PM=%5.0f -> 흡기/배기 %s (%s)' % (d.time_s, d.pm, d.combo, d.reason))
        print('추정 누적 에너지 %.1f J' % e)
    else:
        dl = float(sys.argv[1]) if len(sys.argv) > 1 else None
        p = P.plan(dl) if dl else None
        print(json.dumps(p, ensure_ascii=False, indent=1) if p else 'usage: fan_controller.py <PM20까지 마감 초> | <CSV> [마감 초]')
