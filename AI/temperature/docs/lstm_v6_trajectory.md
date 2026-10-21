# LSTM v6: 상한·상승률·에너지 통합 후보

## 설계 순서

v4에서 급상승 예측 지연을 확인하고 v5에서 정책 왕복을 줄였다. v6는 단일 5초 온도뿐 아니라 1/3/5초 온도 궤적과 향후 상한 위험을 함께 학습한다. 제어기는 26°C 상한을 먼저 확인하고 양의 상승률·예상 회복 에너지·PWM 변경 비용을 고려한다. 고정 25.x°C 목표를 추가한 모델은 아니다.

모델은 8개 샘플, 10개 입력 특성, LSTM 은닉 32를 사용한다. 절대 온도와 상대 온도를 함께 사용하며 Heat_Load는 입력하지 않는다. 데이터와 run 분할은 v4/v5와 같다. 증강은 적용하지 않았다. 데이터가 충분하거나 증강이 효과 없다고 검증한 것은 아니므로 후속 실험에서는 train에만 증강한 대조군이 필요하다.

## validation에서 고정한 선택

- 팬 단조성 손실 가중치 0 / 0.2를 비교해 0을 선택했다. 단조성 보장은 없다.
- 예측 여유는 validation 과소예측 잔차의 90% 분위수이며 보장된 신뢰구간이 아니다.
- 새 정책의 보조 OFF/ON을 validation에서 비교했다. 선제 상향 성공 1/5→4/5, 무가열 상향 모두 0회여서 ON을 선택했다.
- 모델 메타데이터에 v6 정책·권장 보조 ON을 기록한 뒤 test를 실행했다.

## 개발 test 결과

기존은 v4 가중치 + v5 정책 + 원본 상승률 보조 ON이다. 신규는 v6 + 궤적 정책 + 보조 ON이다. 모델과 정책을 함께 변경한 비교이므로 개선 원인을 모델 하나로 귀속하지 않는다.

| 지표 | 기존 | v6 |
|---|---:|---:|
| 5초 MAE | 0.0685°C | 0.1758°C |
| 5초 RMSE | 0.2559°C | 0.3323°C |
| 오탐 / 미탐 | 1 / 18 | 6 / 17 |
| 실제 초과 전 첫 팬 상향 | 1/5 | 3/5 |
| 최초 상향 평균 선행시간 | 0.199초 | 0.798초 |
| PWM 변경 | 14 | 15 |
| 무가열 상향 | 0 | 0 |

v6의 1/3/5초 MAE는 0.0244 / 0.1019 / 0.1758°C다. 일부 조기 상향은 온도 상승보다 기록된 PWM 전이에 반응했을 가능성이 있다. 고출력보다 저출력을 더 차갑게 예측하는 후보도 남아 있다. 따라서 최종 배포 모델 대체는 보류하며, v6는 비교·개선용 후보로 유지한다.

## 에너지 지표의 의미

상대 전력은 `(intake/100)^3 + (exhaust/100)^3`다. 회복시간이 추정되면 전력×회복시간을 사용한다. 비용 비교에는 최소 5초를 반영하고, 회복시간 미정일 때는 단기 전력 대용값만 사용한다. 회복시간은 미래 하강률의 선형 연장이므로 장기 검증이 필요하다. 총 에너지 최적성이 보장되지 않는다.

기록 재생에서 명령 PWM³을 실제 시간 간격으로 적분하면 기존 802.90, v6 558.83으로 30.4% 낮다. 하지만 명령은 기록 온도를 바꾸지 않았고 두 구성 모두 선제제어이므로 이를 실제 절감률이나 선제제어 대 사후제어 효과로 주장하지 않는다. DB의 실제 threshold 시험과 비교하려면 동일 조건 v6 폐루프 시험이 필요하다.

## 실행 및 재현

```bash
python3 AI/temperature/src/main_runner.py \
  --controller-model lstm --lstm-model-file lstm_temperature_trajectory_v6.pt \
  --dummy --dry-run
python3 AI/temperature/src/train_lstm_v6.py --output AI/temperature/results/v6_training_new
python3 AI/temperature/src/replay_lstm_v4_control.py \
  --model-file AI/temperature/models/lstm_temperature_trajectory_v6.pt \
  --control-policy v6 --raw-rise-assist --split test \
  --output AI/temperature/results/v6_replay_new
```

새로 학습한 파일의 보조 설정은 validation OFF/ON 재생을 비교한 뒤 `select_v6_assistance.py`로 고정한다. `evaluate_trajectory_v6.py`는 고정 test와 기존 구성의 재생을 비교해 실제 대상 시각 기준 예측 그림·PWM 변경 로그·오탐 사례를 출력한다.

현재 CPU 추론으로 후보를 순차 평가한다. Jetson에서 v6의 최악 지연·소비전력·폐루프 안전성은 검증하지 않았으며 로컬 회귀 테스트 통과와 구분한다. 열폭주 예측 모델이 아니라 학습 범위의 온도 초과 예방 HVAC 후보이다.
