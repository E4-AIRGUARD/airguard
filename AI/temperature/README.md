# Temperature forecasting and control

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

학습·평가 결과는 개발 검증이며 실제 폐루프 에너지 절감을 보장하지 않습니다. 이번 변경은 경로·배포 의존성 정리이며 제어 임계값이나 가중치를 바꾸거나 재학습하지 않았습니다. 새 HTML 대시보드는 포함하지 않았습니다.
