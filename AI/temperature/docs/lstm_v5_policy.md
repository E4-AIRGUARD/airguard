# LSTM v5: 예측 정확도와 제어 안정성 분리

## v4에서 이어진 판단

작은 온도 오차보다 잦은 PWM 전환을 먼저 해결한다. 단계 경계의 왕복과 회복시간 추정 실패를 실제 위험으로 취급하는 정책을 수정했다.

1. 정책 진입/해제 기준에 0.2°C 차이를 둔다.
2. 정책 단계가 바뀌어도 현재 PWM을 비교 후보에 남긴다.
3. 실제 상한 위험일 때만 경제적 유지시간을 우회한다.
4. 회복시간이 미정이라는 이유만으로 최대 출력을 선택하지 않는다.

`control_policy_v5.py`를 실행부에 연결하고 모델 메타데이터로 정책을 선택한다. 모델 위험과 실제 급상승 비상 규칙은 구분한다. 재생 도구는 실제 main 루프를 사용하지만 USB 명령은 보내지 않는다.

## 학습 및 평가 결과

정상 구간 오차 손실 가중치 0/1/3을 validation에서 비교한 결과 0이 선택됐다. 최종 v5 예측 가중치는 v4와 동일하다. 따라서 v5를 새 예측 성능 향상 모델이라고 설명하지 않는다. 입력 데이터와 run 분할은 v4와 같고 증강은 적용하지 않았다.

| 구성 | PWM 변경 | 무가열 PWM 변경 | 오탐 / 미탐 |
|---|---:|---:|---:|
| v4 + 기존 정책 | 66 | 46 | 1 / 18 |
| v4 또는 v5 + v5 정책, 보조 OFF | 15 | 0 | 1 / 18 |

v5 정책에서 원본 상승률 보조 ON/OFF도 비교했다. ON은 PWM 변경 14회, 무가열 상향 0회, 실제 초과 전 첫 상향 1/5건이었다. OFF는 선제 성공 0/5건이었다. 보조는 별도 규칙이며 LSTM이 학습한 예측으로 해석하지 않는다.

## 재현

```bash
python3 AI/temperature/src/train_lstm_v5.py --output AI/temperature/results/v5_training_new
python3 AI/temperature/src/replay_lstm_v4_control.py \
  --model-file AI/temperature/models/lstm_temperature_multirate_v5.pt \
  --control-policy v5 --raw-rise-assist --split test \
  --output AI/temperature/results/v5_replay_new
```

`compare_raw_assist_replay.py`로 같은 모델·같은 기록의 보조 OFF/ON을 비교한다. 출력 폴더는 기존 결과를 덮어쓰지 않도록 새 경로를 사용한다.

이 단계는 정책 안정성을 개선했지만 급상승 선제 대응은 부족했다. 다음 v6에서는 미래 1/3/5초 온도와 상승률, 상한 위험을 함께 학습하는 후보를 추가했다.
