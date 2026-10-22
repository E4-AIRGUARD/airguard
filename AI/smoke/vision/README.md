# 이미지(웹캠) 기반 연기 판별 모델

카메라 영상에서 연기가 있는지(`smoke`) 없는지(`no_smoke`)를 판단하는 이진 분류 모델과, Jetson에서 실시간으로 돌리는 코드, 대시보드 알림 연동 코드를 모은 폴더입니다. 모델은 YOLO11n-cls이고 최종 모델은 v4입니다.

## 핵심 정보

| 항목        | 내용                                                                               |
| ----------- | ---------------------------------------------------------------------------------- |
| 모델        | YOLO11n-cls 이진 분류 (파라미터 약 153만 개, 3.2MB)                                |
| 입력 전처리 | 640x360 resize → 흑백 → CLAHE(6.0, 8x8) → INFERNO 컬러맵 (열화상 느낌의 가상 필터) |
| 연기 발생원 | 모기향                                                                             |
| 학습        | Colab T4, 80 epoch, 강한 증강                                                      |
| 데이터      | train 2,590 / val 894 / test 311장 (클래스 2개)                                    |
| 실행 환경   | Jetson Orin Nano, TensorRT FP16 지원                                               |
| 보조 해석   | Grad-CAM으로 모델이 어디를 보는지 확인하며 틀린 장면 유형을 데이터로 보강          |

## 성능 (test 311장, 켜기 기준 0.40)

| 재현율 | 오탐률 | 정밀도 | F1    | AUROC |
| ------ | ------ | ------ | ----- | ----- |
| 92.7%  | 8.5%   | 87.7%  | 0.901 | 0.977 |

| Jetson Orin Nano (입력 640) | `.pt`       | TensorRT FP16 |
| --------------------------- | ----------- | ------------- |
| 추론 평균                   | 35.1~36.1ms | 27.3~28.4ms   |
| 초당 프레임                 | 25.2~25.9   | 31.4~32.5     |
| 전력                        | 6.49W       | 5.66W         |

이전 모델(v3)과의 비교, 전체 표, 측정 한계는 [`docs/`](docs/)에 있습니다.

## 알려진 한계

- 모기향이나 받침처럼 학습 영상에서 연기와 같이 보였던 물체가 새 위치에 놓이면 연기로 오인할 수 있습니다 (모기향 의존). v4에서 줄었지만 완전히 해결되지는 않았습니다.
- 같은 카메라와 같은 아크릴 박스 환경에서만 확인했고, 다른 환경(공장 등)은 확인하지 않았습니다.
- 켜기 기준 0.40은 오탐이 적은 대신 옅은 연기를 놓칠 수 있습니다. 더 민감하게 하려면 0.30~0.35를 쓰되 오탐이 늘어납니다 (`docs/optimization_summary.md`).

## 폴더 구조

```
vision/
├─ README.md
├─ requirements.txt
├─ models/                       최종 모델 (smoke_cls_v4.pt) 과 변환 방법
├─ src/
│  ├─ realtime_demo_cls.py       실시간 판별 데모 (화면에 smoke / no smoke와 점수 표시)
│  └─ record_clip.py             카메라 영상 녹화
├─ training/
│  └─ train_colab_cls_v4.py      Colab 학습 스크립트
├─ benchmarks/
│  ├─ bench_speed.py             속도 측정, 점수 저장
│  ├─ tune_rule.py               경보 규칙(켜기 기준 등) 비교
│  └─ results/                   측정 결과 (속도 csv, 점수 json, tegrastats 로그, 경보 규칙 표)
├─ docs/                         최적화 정리, 벤치마크 표, v3 비교
└─ data/                         데이터셋 설명 (이미지는 올리지 않음)
```

## 빠른 시작

```
pip install -r requirements.txt

# 웹캠 (카메라가 거꾸로 달린 경우 --rotate 180)
python src/realtime_demo_cls.py --model models/smoke_cls_v4.pt --source 0

# Jetson GPU
python src/realtime_demo_cls.py --model models/smoke_cls_v4.pt --source 0 --device 0 --rotate 180

# 영상 파일
python src/realtime_demo_cls.py --model models/smoke_cls_v4.pt --source video.mp4
```

깜빡임을 줄이려고 점수 평균(EMA 0.3), 켜기 0.40 / 끄기 0.20 분리 기준, 최소 2초 유지를 씁니다(옵션 `--on --off --alpha --hold`).

### Jetson에서 TensorRT로 빠르게

`models/README.md`의 변환 명령을 쓰고 `--model`에 `.engine` 파일을 넘깁니다. `.engine`은 기기마다 직접 변환해야 합니다.

### 대시보드 알림 연동

`embedded/dashboard/integrations/smoke/`를 참고합니다. Jetson에서 `smoke_alert.py`를 실행하고 대시보드에 `smoke-alert.js`를 한 줄 추가하면 됩니다.

## 속도·정확도 다시 재기

```
python benchmarks/bench_speed.py --model models/smoke_cls_v4.pt --video <영상> --device 0 --tag v4_pt640 --dump scores_v4_pt640.json
python benchmarks/tune_rule.py scores_v4_pt640.json grid.csv
```

`tune_rule.py`의 연기 구간(`SMOKE`)과 제외 구간(`EXCL`)은 측정에 쓴 영상(cmp2) 기준입니다. 다른 영상에는 직접 바꿔야 합니다.
