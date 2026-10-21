# Temperature forecasting and control

## v5 제어 정책

`models/lstm_temperature_multirate_v5.pt`는 5초 예측·8표본 입력이며 v4와 같은 모델 구조를 사용합니다. 재학습 후보 중 validation 미탐을 우선해 같은 예측 가중치를 선택했고, 주요 개선은 정책 히스테리시스·현재 조합 보존·상한 위험 분리·회복 불확실 처리입니다. `src/control_policy_v5.py`도 배포해야 합니다.

```bash
python3 AI/temperature/src/main_runner.py --controller-model lstm \
  --lstm-model-file lstm_temperature_multirate_v5.pt --control-policy v5 --dummy --dry-run
```

기본 `--control-policy auto`도 v5 파일의 metadata로 새 정책을 선택합니다. 기존 모델은 이전 정책을 유지합니다. 모델만 바꿔 예측 정확도가 향상됐다고 해석하지 않습니다. 현재 개선은 기록 데이터 재생에서 확인했으며 실제 팬 연결 시험은 별도로 필요합니다.

## Pull 후 실행

레포 루트에서 아래 명령을 실행합니다. Python 3.10 이상과 호환되는 PyTorch가 필요합니다. Jetson은 JetPack 호환 PyTorch를 별도로 설치합니다. 기존 환경의 패키지를 바꾸기 전 가상환경 사용을 권장합니다.

```bash
python3 -m pip install 'numpy>=1.26,<2' 'pandas>=2.1,<3' 'joblib>=1.3,<2' 'pyserial>=3.5,<4' 'scikit-learn>=1.4,<2' 'matplotlib>=3.8,<4'
python3 -m unittest discover -s AI/temperature/tests -v
python3 AI/temperature/src/main_runner.py --controller-model physics --dummy --dry-run
```

최신 v4 가상 센서 실행:

```bash
python3 AI/temperature/src/main_runner.py \
  --controller-model lstm --lstm-model-file lstm_temperature_multirate_v4.pt \
  --raw-rise-assist --stabilize-lstm --dummy --dry-run
```

실제 센서는 `--dummy` 대신 `--serial-port /dev/ttyUSB0`를 지정합니다. `--dry-run`을 유지하면 팬 명령은 전송하지 않습니다. 실제 팬 시험은 준비 후 해당 옵션을 제거합니다. Ctrl+C로 정상 종료합니다. 장치 권한과 ESP32 펌웨어 설정은 별도로 필요합니다.

모델·기본 DB 경로는 스크립트 위치 기준이므로 셸 작업 폴더에 의존하지 않습니다. 기본 DB는 `AI/temperature/runtime/edge_events.db`이며 자동 생성합니다. 사용자가 명시한 상대 경로 옵션은 현재 작업 폴더 기준입니다. 기본 LSTM 파일은 이전 10초 모델이므로 v4 시험은 파일명을 명시합니다.

## Streamlit 대시보드

메인과 별도 터미널에서 실행합니다. 기본 DB는 메인과 같은 `AI/temperature/runtime/edge_events.db`입니다. 별도 DB로 시험했다면 `-- --database <경로>`를 뒤에 지정합니다.

```bash
python3 -m pip install 'streamlit>=1.37,<2'
python3 -m streamlit run AI/temperature/web/dashboard.py
```

대시보드는 센서·팬 피드백·제어 기록을 표시하며 직접 팬을 제어하지 않습니다. DB와 새 HTML 대시보드는 커밋하지 않습니다.

## 데이터 구조

```text
data/
  raw/training_data_v2.csv
  raw/heat4-6/*.csv
  processed/heat4_heat6_plus_normal_fan.csv
  splits/run_manifest.csv
models/
  physics_mpc_model.pkl
  lstm_temperature_model.pt
  lstm_temperature_model_v2.pt
  lstm_temperature_stable_v3.pt
  lstm_temperature_multirate_v4.pt
```

원본은 보존하며 학습에 사용하는 통합 CSV와 실험별 분할을 분리합니다. 30초 가열 데이터는 최신 학습에 포함하지 않습니다. `src/project_paths.py`에서 공통 입력 경로를 관리합니다.

통합 데이터는 6,218행·36 run이며 분할은 train 19 / validation 8 / test 9입니다. 기존 가열 전용 27 run 분할은 `data/splits/heat_only_run_manifest.csv`에 보존했습니다. 이 파일을 정상 운전 포함 학습의 분할로 사용하지 않습니다.

## 학습·평가

```bash
python3 AI/temperature/src/train_heat4_heat6_runtime.py --output AI/temperature/results/baseline_new
python3 AI/temperature/src/train_multirate_rise_lstm.py --output AI/temperature/results/v4_new
python3 AI/temperature/src/compare_event_detection.py --include-v4 --output AI/temperature/results/events_new
```

기존 결과를 덮어쓰지 않도록 새 출력 폴더를 사용합니다. v4 학습 비교에는 배포된 Physics·v2·v3 모델이 필요합니다. 사건 평가는 기본적으로 `models/`의 v4를 사용하며, 새 학습 결과는 `--model-file AI/temperature/results/v4_new/models/lstm_temperature_multirate_v4.pt`로 지정합니다. v3 개선 스크립트는 실제 DB 분석을 포함하므로 `--database <시험 DB>`가 필수입니다. 개인 PC의 Downloads 경로는 기본값으로 사용하지 않습니다.

학습·평가 결과는 개발 검증이며 실제 폐루프 에너지 절감을 보장하지 않습니다. 새 HTML 대시보드는 포함하지 않았습니다.

## v6 궤적 예측·상승률 억제 후보

현재 전력 계산은 [RPM 기반 추정식](docs/fan_power_estimation.md)으로 통일했습니다. 과거 버전 비교 표는 변경 전 PWM³ 상대 지표를 사용한 결과이므로 새 정책의 에너지 결과와 혼용하지 않습니다. 모델 재학습 없이 후보 전력 평가만 변경했으며, Jetson에는 `src/fan_power.py`도 함께 배포해야 합니다.

설계와 검증의 진행 순서는 [v4 기준 모델](docs/lstm_v4_baseline.md) → [v5 정책 안정화](docs/lstm_v5_policy.md) → [v6 궤적·상승률 제어](docs/lstm_v6_trajectory.md)에 기록했습니다. 각 문서에는 채택 이유뿐 아니라 미해결 문제와 증강 적용 여부도 포함합니다.

v6는 최근 8개 샘플에서 1/3/5초 뒤 온도와 향후 상한 위험을 함께 학습합니다. 제어기는 26°C 상한 여유를 먼저 확인하고, 양의 상승률·예상 회복 에너지·PWM 변경 비용으로 후보를 비교합니다. 회복시간을 추정하지 못하면 총 에너지는 미정으로 남기고 단기 전력 대용값만 사용합니다. 검증 데이터에서는 원본 상승률 보조 ON을 선택했습니다. 해당 설정은 모델 파일에 저장됩니다.

```bash
python3 AI/temperature/src/main_runner.py \
  --controller-model lstm --lstm-model-file lstm_temperature_trajectory_v6.pt \
  --dummy --dry-run
```

정책은 `--control-policy auto`로 v6가 자동 선택됩니다. 보조제어는 `--no-raw-rise-assist`로 명시적으로 해제할 수 있습니다. 실제 센서는 `--dummy`를 `--serial-port /dev/ttyUSB0`으로 바꾸며, 팬 전송 전에는 `--dry-run`을 유지합니다. v6 실행에는 기존 런타임 파일 외에 `trajectory_lstm_v6.py`, `trajectory_control_v6.py`, `control_policy_v5.py`와 갱신된 `main_runner.py`가 필요합니다.

```bash
python3 AI/temperature/src/train_lstm_v6.py --output AI/temperature/results/v6_training_new
python3 AI/temperature/src/replay_lstm_v4_control.py \
  --model-file AI/temperature/models/lstm_temperature_trajectory_v6.pt \
  --control-policy v6 --raw-rise-assist --split test \
  --output AI/temperature/results/v6_test_new
```

학습 스크립트는 모델 선택에 validation만 사용합니다. 보조제어 선택은 validation OFF/ON 재생 후 `compare_raw_assist_replay.py`, `select_v6_assistance.py` 순서로 수행합니다. 배포된 v6 파일은 그 선택까지 반영한 파일입니다. `evaluate_trajectory_v6.py`는 고정된 test 재생과 기존 모델 재생을 비교합니다.

현재 개발 test에서 v6는 선제 팬 상향 사건이 1/5→3/5로 늘었지만, 5초 MAE는 0.069→0.176°C, 오탐은 1→6건으로 악화되었습니다. 따라서 v6는 비교·개선용 후보이며 기존 모델을 대체할 최종 모델로 확정하지 않습니다. 재생 PWM은 기록된 온도를 바꾸지 않으므로 실제 냉각·소비전력·에너지 절감 검증을 대신하지 않습니다.
