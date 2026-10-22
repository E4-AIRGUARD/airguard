# 분류 모델 v4 학습 (모기향 합성 사진 추가: 모기향만 있는 연기없음 / 모기향 없는 연기있음) (코랩, T4 GPU): 손·플라스틱·햇빛·그림자 "연기 없음" 사진 500장을 추가한 데이터(ds_cls)로 C 설정 학습
# 셀 2에서 unzip할 파일: /content/smoke_cls_dataset_v4.zip
# 셀 1: !pip -q install ultralytics scikit-learn   셀 2: !unzip -q -o /content/smoke_cls_dataset_v4.zip -d /content/   셀 3: 이 코드 통째로 실행
# 이번에는 C 설정 하나만 학습해요 (바꾸지 마세요)
RUN = 'C_n_strongaug'
import sympy, sympy.printing  # 코랩에서 sympy 오류(AttributeError)가 날 때를 대비한 줄
import glob, os, numpy as np, torch
from ultralytics import YOLO
from sklearn.metrics import roc_auc_score
assert torch.cuda.is_available(), "런타임 유형을 T4 GPU로 바꾸세요."

CONFIGS = {
  'A_base_n_640':      dict(model='yolo11n-cls.pt', imgsz=640, epochs=60),                                   # 지난번과 같은 설정(기준선)
  'B_small_640':       dict(model='yolo11s-cls.pt', imgsz=640, epochs=60),                                   # 모델 키우기
  'C_n_strongaug':     dict(model='yolo11n-cls.pt', imgsz=640, epochs=80, degrees=15, translate=0.2, scale=0.5,
                            hsv_v=0.4, erasing=0.4, mixup=0.1, label_smoothing=0.1),                         # 증강 강화 + 라벨 스무딩
  'D_small_strongaug': dict(model='yolo11s-cls.pt', imgsz=640, epochs=80, degrees=15, translate=0.2, scale=0.5,
                            hsv_v=0.4, erasing=0.4, mixup=0.1, label_smoothing=0.1),
}
common = dict(data='/content/ds_cls', batch=32, device=0, patience=25, project='/content/runs', workers=2, cache=False,
              fliplr=0.5, hsv_h=0.0, hsv_s=0.0, seed=0)

# 연기가 전혀 없는 영상(진짜 오탐을 재는 용도): 파일명에 이 영상 이름이 들어간 사진
PURE_NO_SMOKE = ['20261016_141951_25d9a9e5_empty', '20261016_140524_00d596e2', '20261016_140210_c183d185', '20261015_161730_3300b20a', 'N03_shadow_reflection', 'jetson_empty', 'jetson_hand', 'jetson_hand2', 'jetson_hand3', 'syn_coilpaste', 'syn_inpaintctl']

def scores(model, split):
    sm = [k for k, v in model.names.items() if v == 'smoke'][0]; out = []
    for lab, y in [('smoke', 1), ('no_smoke', 0)]:
        for p in sorted(glob.glob(f'/content/ds_cls/{split}/{lab}/*.jpg')):
            out.append((os.path.basename(p), y, float(model.predict(p, imgsz=640, verbose=False)[0].probs.data[sm])))
    return out

results = {}
for name, cfg in [(RUN, CONFIGS[RUN])]:
    kw = {**common, **cfg}; mdl = kw.pop('model')
    YOLO(mdl).train(name=name, **kw)
    best = YOLO(f'/content/runs/{name}/weights/best.pt')
    val, test = scores(best, 'val'), scores(best, 'test')
    yv, sv = np.array([x[1] for x in val]), np.array([x[2] for x in val])
    ths = np.arange(0.05, 0.96, 0.05)
    def f1(y, s, t):
        p = s >= t; tp = (p & (y == 1)).sum(); fp = (p & (y == 0)).sum(); fn = (~p & (y == 1)).sum(); return 2 * tp / max(1, 2 * tp + fp + fn)
    t_best = float(max(ths, key=lambda t: f1(yv, sv, t)))          # 기준은 val에서만 고름
    yt, st = np.array([x[1] for x in test]), np.array([x[2] for x in test]); p = st >= t_best
    pure = np.array([any(n in x[0] for n in PURE_NO_SMOKE) for x in test + val])
    allp = np.array([x[2] for x in test + val]) >= t_best; ally = np.array([x[1] for x in test + val])
    results[name] = dict(thr=t_best, val_auroc=roc_auc_score(yv, sv), test_auroc=roc_auc_score(yt, st),
        test_hit=f'{int((p & (yt == 1)).sum())}/{int(yt.sum())}', test_fp=f'{int((p & (yt == 0)).sum())}/{int((yt == 0).sum())}',
        test_f1=round(float(f1(yt, st, t_best)), 3),
        pure_fp=f'{int((allp & pure & (ally == 0)).sum())}/{int((pure & (ally == 0)).sum())}')
    print(name, results[name], flush=True)
    import json; open('/content/results.jsonl', 'a').write(json.dumps({name: {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in results[name].items()}}, ensure_ascii=False) + '\n')
    del best, val, test; import gc; gc.collect(); torch.cuda.empty_cache()

# 끝나자마자 모델을 내 컴퓨터로 내려받아요 (런타임이 끊기면 파일이 사라지기 때문)
try:
    from google.colab import files; files.download(f'/content/runs/{RUN}/weights/best.pt')
except Exception as e: print('자동 다운로드 실패, 파일 패널에서 직접 받으세요:', e)
print('\n누적 결과 파일: /content/results.jsonl  (셀에서 !cat /content/results.jsonl)')
print('\n설정 | val기준 | val AUROC | test AUROC | test 연기잡음 | test 오탐 | test F1 | 연기없는영상 오탐')
for n, r in results.items():
    print(f"{n} | {r['thr']:.2f} | {r['val_auroc']:.3f} | {r['test_auroc']:.3f} | {r['test_hit']} | {r['test_fp']} | {r['test_f1']} | {r['pure_fp']}")
