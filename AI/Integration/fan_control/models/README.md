# models

| 파일 | 설명 |
|---|---|
| `model.json` | 최종 모델 (회귀, 학습 161행). 제어기 기본값 |
| `model_시간표_옛20회.json` | 통계 시간표 백업 (`--model`로 선택) |
| `archive/model_회귀_92회_이전.json` | 이전 회귀 모델 (92행) |
| `archive/model_회귀_10-20반영_이전.json` | 이전 회귀 모델 (65행) |
| `archive/model_회귀_38회_이전.json` | 이전 회귀 모델 (38행) |

- 형식: JSON (신경망 가중치 없음, 계수 5개와 조합별 시간·전력 표). 약 10KB
- 식: 1/T = k0 + k1 s(in) + k2 s(out) + k3 s(in)s(out), s(x) = x/(x+h), k = (-0.494, 0.5703, 0.5801, -0.6443), h = 0.10
- 학습: `training/fan_model_regression.py` (데이터는 `data/arrival_times/`)
- TensorRT 변환 대상이 아니에요. 자세한 내용은 [`docs/모델_최적화_및_변환_절차.md`](../docs/모델_최적화_및_변환_절차.md)
