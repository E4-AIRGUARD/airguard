"""Jetson 속도·정확도 벤치마크. 같은 영상 프레임으로 모델 하나의 속도를 재고, 원하면 프레임별 점수도 저장해요.
사용 예
  python3 bench_speed.py --model smoke_cls_v7.pt --video cmp2.mp4 --imgsz 640 --device 0 --tag pt640 --dump scores_pt640.json
  python3 bench_speed.py --model smoke_cls_v7_640.engine --video cmp2.mp4 --imgsz 640 --device 0 --tag trt640 --dump scores_trt640.json
결과: bench_results.csv에 한 줄씩 쌓여요. (--dump는 영상 전체를 2프레임마다 채점하므로 시간이 걸려요)
"""
import argparse, csv, json, os, time
import cv2, numpy as np
from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument('--model', required=True); ap.add_argument('--video', required=True)
ap.add_argument('--imgsz', type=int, default=640); ap.add_argument('--device', default='0')
ap.add_argument('--n', type=int, default=200, help='속도 측정에 쓸 프레임 수'); ap.add_argument('--warmup', type=int, default=20)
ap.add_argument('--repeat', type=int, default=3, help='같은 프레임들을 몇 번 반복할지')
ap.add_argument('--tag', default='run'); ap.add_argument('--dump', default='')
a = ap.parse_args()

clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))
def thermal(bgr):   # realtime_demo_cls.py와 같은 전처리
    return cv2.applyColorMap(clahe.apply(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)), cv2.COLORMAP_INFERNO)
def prep(f):
    return thermal(cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA))

model = YOLO(a.model, task='classify')
dev = int(a.device) if a.device.isdigit() else a.device
sm = [k for k, v in model.names.items() if v == 'smoke'][0]
cap = cv2.VideoCapture(a.video); total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fps = cap.get(cv2.CAP_PROP_FPS) or 10
step = max(1, total // a.n); frames = []
for i in range(0, total, step):
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, f = cap.read()
    if ok: frames.append(f)
    if len(frames) >= a.n: break

def predict(x):
    return float(model.predict(x, imgsz=a.imgsz, verbose=False, device=dev)[0].probs.data[sm])

for f in frames[:a.warmup]: predict(prep(f))                      # 워밍업 (시간에 안 넣음)
pre, inf, tot = [], [], []
for _ in range(a.repeat):
    for f in frames:
        t0 = time.perf_counter(); x = prep(f); t1 = time.perf_counter(); predict(x); t2 = time.perf_counter()
        pre.append((t1 - t0) * 1000); inf.append((t2 - t1) * 1000); tot.append((t2 - t0) * 1000)
def st(v): return dict(mean=float(np.mean(v)), p50=float(np.percentile(v, 50)), p95=float(np.percentile(v, 95)), max=float(np.max(v)))
r = dict(tag=a.tag, model=os.path.basename(a.model), imgsz=a.imgsz, frames=len(frames) * a.repeat,
         pre_ms=st(pre)['mean'], infer_ms_mean=st(inf)['mean'], infer_ms_p50=st(inf)['p50'], infer_ms_p95=st(inf)['p95'],
         infer_ms_max=st(inf)['max'], total_ms_mean=st(tot)['mean'], fps_total=1000 / st(tot)['mean'])
try:
    import torch
    r['gpu_mem_mb'] = round(torch.cuda.max_memory_allocated() / 2**20, 1) if torch.cuda.is_available() else ''
except Exception:
    r['gpu_mem_mb'] = ''
print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
new = not os.path.exists('bench_results.csv')
with open('bench_results.csv', 'a', newline='', encoding='utf-8-sig') as fh:
    w = csv.DictWriter(fh, fieldnames=list(r.keys()))
    if new: w.writeheader()
    w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})

if a.dump:   # 정확도 비교용: 영상 전체를 2프레임마다 채점
    cap = cv2.VideoCapture(a.video); out = {'fps': fps, 't': [], 'score': []}; i = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if i % 2 == 0: out['t'].append(i / fps); out['score'].append(predict(prep(f)))
        i += 1
    json.dump(out, open(a.dump, 'w')); print('saved', a.dump, len(out['t']))
