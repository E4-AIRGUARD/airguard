# Temperature — 온도 예측·팬 제어

챔버 센서의 최근 온도 변화로 **1·3·5초 뒤 온도를 예측하고, 26°C 상한 위험과 예상 상승률·회복 에너지 효율을 고려해 흡기·배기 팬을 자동제어**하는 모듈입니다. 실제 운전에는 ESP32에 연결된 온도 센서·팬과 호환 패키지가 필요합니다.

## 1. 주요 기능

| 단계 | 기능 |
|---|---|
| 수집 | ESP32 USB Serial로 온도·습도·PM2.5·실제 PWM/RPM 수신 |
| 전처리 | 최근 8표본 버퍼, 최근 3표본 중앙값 평활화, 원본 1·3·5초 변화율 생성 |
| 예측 | LSTM v6로 1·3·5초 미래 온도와 상한 위험 점수 추정 |
| 후보 비교 | 흡기·배기 각각 35/60/100%의 **9개 전체 조합** 평가 |
| 제어 | 상한 위험 우선, 상승률·추정 전력·회복 예상시간·변경 비용 비교 |
| 보조·안정화 | 원본 상승률 보조, 비상 대응, 일반 변경의 15초 유지·5회 확인·10% 초과 개선 조건 |
| 기록 | 센서값·판단·명령·장치 피드백·모델 정보를 로컬 SQLite에 저장 |

LSTM은 온도를 예측하고 별도 제어기가 PWM을 선택합니다. 일반 후보 비교는 약 1초마다 수행하며, 상승률 보조·비상·버퍼 준비 경로에서는 고정 명령이 우선할 수 있습니다. 습도·PM2.5는 기록하지만 이 온도 모델의 학습 입력이나 연기 판정에는 사용하지 않습니다. 대시보드용 읽기 전용 알림 API를 포함하며 HTML 화면은 포함하지 않습니다.

## 2. 폴더 구조와 파일별 역할

```text
temperature/
├─ run.py                       폴더 단독 실행 진입점
├─ README.md
├─ .gitignore                   생성 DB·학습 출력·캐시 제외
├─ src/                         센서 수신·추론·팬 제어·기록
├─ models/                      배포 가중치·비교 회귀 모델
├─ training/                    학습 및 학습 입력 생성
├─ benchmarks/
│  ├─ validate.py               동일 test 예측 재평가
│  └─ results/                  지표 JSON·run별 지표·시점별 예측
├─ tests/                       후보·전력·모델 무결성 테스트
├─ data/
│  ├─ processed/heat4_heat6_plus_normal_fan.csv
│  └─ splits/run_manifest.csv
└─ docs/
   ├─ INTEGRATION.md            팀원 통합 메인 연결 방법
   ├─ API.md                    대시보드 알림·이상/회복 기록 API
   ├─ 모델_평가서_온도.md       학습·평가 지표·오탐·미탐 분석
   ├─ Benchmark_결과_온도.md    확보된 성과·추가 측정 항목
   └─ assets/                  예측·실제 시험 그래프
```

### 실행 코드 (`src/`)

| 파일 | 역할 |
|---|---|
| `main_runner.py` | 센서·판단·송신·DB 실행 루프, Ctrl+C 종료 처리 |
| `temperature_controller.py` | 온도 제어 설정과 우선순위, observe/decide/confirm_applied 인터페이스 |
| `lstm_controller.py` | 배포 pt 해시·입력 규격 검사, CPU 추론, 9후보 목록 |
| `trajectory_lstm_v6.py` | 다중 시점 LSTM 신경망 구조 |
| `trajectory_control.py` | 후보별 상한·상승률·회복시간·비용 계산, 명령 변경 게이트 |
| `control_types.py` | 예측·후보 결과 자료형, 전체 후보 비교 요약 |
| `raw_rise_assist.py` | 원본 온도 변화율 기반 빠른 예방 냉각 보조 |
| `temperature_features.py` | 실제 시간 간격에 따른 다중 변화율 계산 |
| `sensor_buffer.py` | 최근 표본 관리, 인과적 평활화, 입력 행렬 생성 |
| `sensor_receiver.py` | ESP32 Serial 수신·파싱, 같은 연결을 이용한 송신 |
| `fan_control.py` | 흡기·배기 PWM 명령 인코딩·전송, dry-run |
| `fan_power.py` | RPM 기반 추정 전력·회복 에너지·관측 구간 적분 |
| `event_store.py` | 센서·제어 상태·변경 사건·모델 메타데이터 SQLite 기록 |
| `dashboard_api.py` | SQLite 읽기 전용 센서·예측·제어·이벤트 HTTP API |
| `temperature_alert.py` | 알림 API 서버 실행 진입점 |
| `temperature_events.py` | 기록된 온도에서 상승·초과·회복 사건 재구성 |
| `project_paths.py` | 실행 위치와 무관한 폴더 내부 데이터·모델·결과 경로 |

### 모델·학습·평가

| 파일 | 역할 |
|---|---|
| `models/lstm_temperature_trajectory_v6.pt` | 독립 실행에서 사용하는 LSTM v6 배포 가중치 |
| `models/physics_mpc_model.pkl` | v6와 동일 train run으로 재학습한 제약 회귀 baseline. 비교용이며 독립 실행 제어기는 아님 |
| `training/train_lstm_v6.py` | 원본 고정 분할로 v6 학습, validation 선택 결과 저장 |
| `training/prepare_examples.py` | 버퍼·미래 정답·PWM 유지 조건을 반영한 학습 사례 생성 |
| `training/train_physics_v6.py` | 고정 train run만 사용해 기본 회귀 pkl 재생성 |
| `training/physics_regression.py` | 회귀 변화율·계수 학습 함수 |
| `benchmarks/validate.py` | 두 배포 모델의 공통 5초 test 성능 재현. 속도 측정 코드는 아님 |
| `tests/test_nine_candidates.py` | 9후보 전체 평가·신규 후보 선택·기록·명령 수용 검증 |
| `tests/test_fan_power.py` | 전력 단위·보정 RPM·결측·공백 적분 검증 |
| `tests/test_release_models.py` | 모델 로딩·데이터 해시·회귀 재생성 일치 검증 |
| `tests/test_temperature_alert_contract.py` | 상승·회복·공백·stale·읽기 전용 계약 검증 |
| `tests/test_dashboard_http.py` | HTTP 경로·CORS·입력 오류·쓰기 거부 검증 |

## 3. 독립 실행

아래 명령은 모두 **`temperature` 폴더 안에서** 실행합니다.

### 환경 확인

Python 3.10 이상과 Jetson 호환 PyTorch, NumPy, pyserial이 필요합니다. `python3`는 해당 패키지가 설치된 환경이어야 합니다.

```bash
python3 -c "import sys, torch, numpy, serial; print(sys.executable); print(torch.__version__, numpy.__version__)"
```

추가 학습·평가·전체 테스트에는 pandas·scikit-learn·joblib이 필요합니다.

### 센서 연결 확인 — 팬 명령 미전송

```bash
python3 run.py --serial-port /dev/ttyUSB0 --dry-run
```

`--dry-run`도 실제 센서는 필요합니다. 더미 센서 모드가 아닙니다. 이 폴더에 시뮬레이션용 가짜 온도는 제공하지 않습니다.

### 센서·팬 자동제어

```bash
python3 run.py --serial-port /dev/ttyUSB0
```

포트를 생략하면 자동 탐색합니다. ESP32 통신속도는 115200이며 실제 장치 경로를 확인해야 합니다. 같은 포트를 다른 수집기·시리얼 모니터·통합 메인이 동시에 열면 안 됩니다.

```bash
python3 run.py --help
python3 run.py --serial-port /dev/ttyUSB0 --database runtime/my_test.db
```

지원 옵션은 `--serial-port`, `--database`, `--dry-run`입니다. 모델은 v6로 고정하며 기본 출력 시점은 5초입니다. 이전 개발본의 `--controller-model`, `--horizon`, `--trial-id` 옵션은 지원하지 않습니다.

기본 DB는 **이 폴더 기준** `runtime/edge_events.db`로 자동 생성합니다. 사용자 지정 상대 DB 경로는 실행한 터미널 위치 기준입니다. 주요 테이블은 `sensor_readings`, `control_status`, `control_events_v2`, `runtime_metadata`입니다. 시험용 가열 키보드 입력과 closed_loop_trial 표는 제공하지 않습니다.

Ctrl+C 종료 시 0/0 정지 명령을 시도합니다. 센서 5초 지연 시 기존 정책은 35/35입니다. 이는 검증된 화재·센서 장애 안전정책이 아니므로 연기 모듈 병합 시 우선순위와 종료 동작을 합의해야 합니다.

## 4. 재학습·평가·테스트

```bash
# 회귀 pkl을 기본 models/ 경로에 덮어씁니다.
python3 training/train_physics_v6.py

# 배포 pt는 바꾸지 않고 새 결과 폴더에 저장합니다.
python3 training/train_lstm_v6.py --output results/v6_retrain

# 실제 센서 없이 배포 모델의 동일 test 예측 성능을 재평가합니다.
python3 benchmarks/validate.py --output results/revalidation

# 실제 센서·팬에 접근하지 않는 자동 테스트
python3 -m unittest discover -s tests -p 'test_*.py'
```

학습·평가 출력 폴더가 이미 존재하면 덮어쓰지 않고 실패하므로 새 이름을 지정합니다. 회귀만 별도 출력하려면 `--output results/physics_new.pkl`을 사용합니다. LSTM 재학습 결과를 운전에 적용하려면 평가·승인 후 pt와 `src/lstm_controller.py`의 허용 SHA-256을 함께 갱신해야 합니다. 해시 검사만 제거하지 마세요.

## 5. 데이터·성능 요약

4초 가열 9 run, 6초 가열 18 run, 정상 팬 운전 9 run으로 총 **36 run·6,218행**입니다. train/validation/test는 **19/8/9 run**으로 나누고 정규화는 train에서만 계산했습니다. 30초 가열과 추가 증강 사본은 사용하지 않았습니다. test는 반복 확인한 개발 평가셋이며 독립 최종 시험은 아닙니다.

| 같은 9 run·1,532시점, 5초 예측 | MAE °C | RMSE °C | 오탐 FP | 미탐 FN |
|---|---:|---:|---:|---:|
| 동일 분할 회귀 baseline | 0.1535 | 0.3317 | 10 | 22 |
| LSTM v6 | 0.1758 | 0.3323 | 6 | 17 |

실제·예측 온도 ≥26°C를 양성으로 정의하며 FP/FN은 사건이 아니라 시점 수입니다. 가열 run MAE는 회귀 0.1756°C 대비 v6 0.1020°C로 낮지만, 정상 run 오차와 전체 MAE는 회귀가 더 좋았습니다. 온도 회귀이므로 객체 탐지 mAP는 적용하지 않습니다.

9후보 확장은 가중치를 바꾸지 않은 정책 변경이므로 이 온도 예측 수치와 새 9후보의 실장 제어 성능을 혼동하지 않습니다. 기존 실장 시험은 6후보 정책이며 모든 구성에서 26°C를 넘었습니다. v6 최초 상향은 원본 상승률 보조의 기여였습니다. 후보 역전·회복시간·총에너지 최적성은 아직 검증이 필요합니다. Jetson 지연·메모리와 ONNX/TensorRT 최적화 전후 결과는 미측정입니다.

추정 전력은 두 팬에 대해 `5 × 0.27 × (RPM/7500)³` W를 합산합니다. 전력계 실측이 아니며 Jetson·센서 등 보드 전력은 제외합니다. 회귀 팬 계수는 모두 0으로 후보별 냉각 최적화용으로 해석하지 않습니다.

## 6. 다른 모듈과 통합

### 대시보드 알림 API

제어기와 별도 터미널에서 **같은 DB**를 지정합니다. 아래 명령은 temperature 폴더 기준입니다.

```bash
python3 src/temperature_alert.py --database runtime/edge_events.db --port 8094 --allow-origin http://127.0.0.1:8091
```

`GET /api/temperature`와 `/api/snapshot`은 현재 센서·예측·제어 후보·알림·운전 기록을,
`GET /api/events?seconds=86400`은 최근 24시간의 이상·회복 기록을 제공합니다.
상세 응답과 화면 바인딩은 [API 안내](docs/API.md)를 참고하세요.
서버는 SQLite 읽기만 수행하며 센서 포트·팬 명령·모델 추론을 실행하지 않습니다.
이 API를 실행하는 것만으로 HTML의 빨간 상태·팝업이 자동 연결되는 것은 아닙니다.

온도 판단과 장치 전송을 분리하는 방법은 [통합 안내](docs/INTEGRATION.md)를 참고하세요. 독립 실행과 통합 메인은 동시에 실행하지 않습니다. 최종 팬 명령의 전송 주체는 하나만 두고, `confirm_applied()`에는 중재 후 실제 전송한 PWM을 전달합니다.

상세 근거는 [모델 평가서](docs/모델_평가서_온도.md), [Benchmark 문서](docs/Benchmark_결과_온도.md), `benchmarks/results/`와 `docs/assets/`에 있습니다.
