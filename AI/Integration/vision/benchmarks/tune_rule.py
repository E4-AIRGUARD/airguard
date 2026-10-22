import json, itertools, csv, sys, numpy as np
d = json.load(open(sys.argv[1])); out = sys.argv[2]
if 'score' in d: d['v4'] = d['score']      # bench_speed.py --dump 형식도 바로 읽어요
T = np.array(d['t'])
SMOKE = [(159.0, 166.0), (174.5, 187.5)]      # 직접 정한 구간(영상 시간)
EXCL = [(166.0, 174.5), (187.5, 205.0)]       # 애매해서 채점 제외
DT = 0.3                                      # 데모: 10fps, 3프레임마다
grid = np.arange(0, T[-1], DT)
def judge(sc, on, off, alpha, hold):
    ema = None; st = False; ton = -1e9; res = []
    for t, s in zip(grid, sc):
        ema = s if ema is None else alpha*s + (1-alpha)*ema
        if not st and ema >= on: st, ton = True, t
        elif st and ema < off and t-ton >= hold: st = False
        if st and ema >= on: ton = t
        res.append(st)
    return np.array(res)
def alarm(st, N):                              # N초 연속 켜져 있어야 경보
    if N <= 0: return st
    out = np.zeros_like(st); run = 0.0
    for i, s in enumerate(st):
        run = run+DT if s else 0.0
        out[i] = run >= N
    return out
def inr(t, rs): return any(a <= t < b for a, b in rs)
neg = np.array([not inr(t, SMOKE) and not inr(t, EXCL) for t in grid])
def evaluate(model, on, off, alpha, hold, N):
    sc = np.interp(grid, T, np.array(d[model]))
    al = alarm(judge(sc, on, off, alpha, hold), N)
    fa_sec = al[neg].sum()*DT
    ev = 0; prev = False
    for a, n in zip(al, neg):
        if a and n and not prev: ev += 1
        prev = a and n
    lat = []; cov = []
    for a0, b0 in SMOKE:
        m = (grid >= a0) & (grid < b0)
        idx = np.where(al & m)[0]
        lat.append(round(grid[idx[0]]-a0, 1) if len(idx) else None)
        cov.append(round(al[m].mean()*100))
    return dict(model=model, on=on, off=off, alpha=alpha, hold=hold, N=N,
                fa_sec=round(fa_sec, 1), fa_events=ev, latA=lat[0], latB=lat[1], covA=cov[0], covB=cov[1])
rows = []
for model in ('v4',):
    ons = (0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
    for on, alpha, hold, N in itertools.product(ons, (0.3, 0.5, 1.0), (1.0, 2.0, 3.0), (0, 1, 2, 3)):
        rows.append(evaluate(model, on, round(on*0.5, 3), alpha, hold, N))
with open(out, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)
print(len(rows), 'rows; negative seconds', round(neg.sum()*DT))
