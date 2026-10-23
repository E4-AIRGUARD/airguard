# Jetson 실행 코드

## 0. 한눈에 보기

Jetson에서는 프로세스 2개가 돌아갑니다. 둘은 HTTP로만 연결됩니다.

| 프로세스         | 역할                                                        | 코드                                                   | 포트 |
| ---------------- | ----------------------------------------------------------- | ------------------------------------------------------ | ---- |
| 통합 제어기      | ESP32 센서 수신 → 온도/PM 판정 → 팬 PWM 전송 → 사건·DB 저장 | `AI/Integration/src/airguard_control/control.py`       | 8095 |
| 카메라 연기 서버 | 카메라 → 전처리 → YOLO11n-cls 추론 → 연기 알림 판정         | `embedded/dashboard/integrations/smoke/smoke_alert.py` | 8092 |

카메라 연기 확률은 **팬 제어 입력이 아닙니다**. 팬은 PMS5003 PM2.5와 BME280 온도로만 움직입니다. 연기 확률은 사건 사진과 알림용입니다. 두 프로세스의 연결은 통합 제어기의 `RecoveryCamera`가 8092의 `/frame.jpg`를 가져와 사건 사진으로 쓰는 부분입니다.

## 1. 코드 구조

전체 흐름은 5단계입니다. **입력 → 전처리 → 추론 → 이벤트 판정 → 장치/DB**

```
입력 → 전처리 → 추론 → 이벤트 판정 → 장치 제어 + DB 저장
```

팬을 움직이는 **센서 경로**와, 연기 알림을 만드는 **카메라 경로**가 따로 있습니다. 두 경로의 코드는 서로 다른 프로세스입니다.

### 1-1. 센서 경로 (팬 제어) : `AI/Integration/src/airguard_control/`

| 단계        | 하는 일                                                                                             | 파일                                                                                              |
| ----------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 입력        | ESP32에서 1초마다 `DATA,…` 줄 수신 (온도·습도·PM2.5·팬 PWM/RPM)                                     | `control.py`의 `SerialOwner`                                                                      |
| 전처리      | 값 범위 검사, 이상값 버림, 최근 8표본 윈도우와 변화율 계산                                          | `sensor_bridge.py`, `temperature/sensor_buffer.py`, `temperature/temperature_features.py`         |
| 추론        | 온도: LSTM v6가 5초 뒤 온도를 예측해 PWM 후보 9개 비교. PM2.5: 회귀 모델로 최소 에너지 팬 조합 선택 | `temperature/temperature_controller.py`, `temperature/lstm_controller.py`, `pm/fan_controller.py` |
| 이벤트 판정 | 온도 ≥ 26°C, PM2.5 > 50 이면 사건 시작, 일정 시간 유지되면 회복                                     | `events/pm_recovery.py`, `events/event_recorder.py`                                               |
| 장치 / DB   | 팬 PWM 명령 전송. 센서·제어 기록 SQLite 저장, Aiven 업로드                                          | `dashboard_lease.py`, `control.py`, `integration_store.py`, `storage/uploader.py`                 |

온도와 PM 두 요구는 `pm/smoke_fan.py`의 `combine_max`가 흡기·배기별로 큰 값을 골라 합칩니다. 전체를 1초마다 묶어 돌리는 곳은 `control.py`의 `Coordinator.step()`입니다.

### 1-2. 카메라 경로 (연기 알림) : `embedded/dashboard/integrations/smoke/smoke_alert.py`

| 단계        | 하는 일                                                                                                                                                                           |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 입력        | USB 웹캠 프레임 읽기 (`cv2.VideoCapture`)                                                                                                                                         |
| 전처리      | 180도 회전 → 640x360 축소 → 흑백 → CLAHE → INFERNO 컬러맵                                                                                                                         |
| 추론        | YOLO11n-cls로 연기 점수 계산 (3프레임마다 1회)                                                                                                                                    |
| 이벤트 판정 | `SmokeJudge`: 점수 평균, 켜기/끄기 기준, 유지 시간, 해제 4초 대기                                                                                                                 |
| 장치 / DB   | 8092 포트 웹서버로 상태·사진 제공 (`/api/smoke`, `/frame.jpg`). main 기준 DB 저장 없음(최근 30건 메모리). 로컬 최신 코드는 영상 사건·사진을 기록 대기열에 저장하고 Aiven으로 올림 |

한계: 카메라 경로는 파일 하나에 모든 단계가 들어 있습니다. 클래스로만 나뉘어 있고 모듈로는 분리되어 있지 않습니다.

### 1-3. 두 경로의 연결

센서 경로의 `events/recovery_camera.py`가 카메라 서버(8092)의 `/frame.jpg`를 가져와 사건 사진으로 저장합니다. 카메라 점수는 팬 제어에 쓰이지 않습니다.

## 2. 실행 환경

| 항목                  | 값                                                                                      |
| --------------------- | --------------------------------------------------------------------------------------- |
| 보드                  | Jetson Orin Nano (Orin Nano 7.6GB, RAM 7620MB 표시)                                     |
| OS/JetPack            | JetPack 6 / Ubuntu 22.04 기준. **정확한 JetPack 마이너/L4T 버전은 확인 불가**           |
| CUDA / cuDNN          | **확인 불가/추가 필요** (`jetson_release`, `nvcc --version` 결과 필요)                  |
| TensorRT              | 10.3.0                                                                                  |
| PyTorch / Ultralytics | torch 2.8.0, Ultralytics 8.4.118 (Jetson 변환·측정 시점)                                |
| Python                | 3.10 이상 (`pyproject.toml`, Jetson 기준 3.10)                                          |
| 전력 모드             | `nvpmodel -m 0` (MAXN) 계획만 기재. 측정 당시 적용 여부 **확인 불가**                   |
| 장치                  | ESP32(CP210x, 115200), PMS5003, BME280, 흡기·배기 PWM 팬, USB 웹캠(`/dev/video0`, MJPG) |
| 네트워크              | Tailscale, Jetson `100.106.99.75`, 사용자 `jetson`                                      |

### 의존성

| 파일                           | 내용                                                                          |
| ------------------------------ | ----------------------------------------------------------------------------- |
| `requirements/control.txt`     | `numpy>=1.24,<3`, `pyserial>=3.5,<4` (PyTorch는 JetPack용 별도 설치)          |
| `requirements/upload.txt`      | `PyMySQL>=1.1,<2`, `python-dotenv>=1,<2` (업로드 시에만)                      |
| 연기 모델용 `requirements.txt` | `ultralytics>=8.3`, `opencv-python>=4.8`, `numpy>=1.24` (`websockets`는 선택) |

Jetson 사전 설정은 `jetson_setup.sh`로 합니다(brltty 제거, dialout 그룹, pyserial, cp210x 확인).

### 모델 파일

| 모델                         | 파일                                                                                     | 비고                                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 온도 LSTM v6                 | `lstm_temperature_trajectory_v6.pt` (29,193B)                                            | 최근 8표본 → 5초 궤적.                                                                     |
| PM 회귀                      | `model.json` (10,401B)                                                                   | 신경망 아님, TensorRT 대상 아님                                                            |
| 연기 분류 (Jetson 실행 모델) | `smoke_cls_v4.pt` → Jetson에서 변환한 `smoke_cls_v4.engine` (YOLO11n-cls, TensorRT FP16) | 10/22 장비 적용 기록에 v4 엔진 사용으로 기재(로컬 기준). `.engine`은 기기별 변환, git 제외 |

GitHub main에는 아직 Jetson이 v7 엔진으로 적혀 있습니다. v4 교체 내용은 로컬 파일에만 있고 GitHub에는 반영되지 않았습니다. v4 교체 후 연속 실행·정확도 재검증 기록은 **확인 불가**입니다. 정적 이미지 평가 수치는 `.pt` 기준이며 TensorRT 엔진·실시간 알림 성능의 재평가 결과가 아닙니다.

## 3. 이벤트 판정 로직

### 3-1. 센서 입력 검증 (`parse_data`)

- 14필드이고 첫 필드가 `DATA`가 아니면 버림. PWM 0~100, RPM ≥ 0 범위 밖이면 버림.
- BME280: 온도 −40~85, 습도 0~100, 기압 300~1100 밖이면 `bme_ok=False`, 값은 None.
- PMS5003: 0~65535 밖이면 `pms_ok=False`.
- 최신 표본이 5초를 넘으면 stale. 타임스탬프가 거꾸로 가면 새 표본으로 취급하지 않음.

### 3-2. 팬 제어 (1초 주기, `Coordinator.step`)

| 규칙               | 값                                                        | 코드            |
| ------------------ | --------------------------------------------------------- | --------------- |
| 허용 PWM           | 35 / 60 / 100 (정지 시 0). 반올림 없이 정확히 일치해야 함 | `valid_pair`    |
| 최종 PWM           | 온도 요구와 PM 요구의 흡기·배기별 최대                    | `combine_max`   |
| 기본 요구          | 온도 유효 표본 없음 → 35/35                               | `step`          |
| 재전송             | 값이 바뀌었거나 5초마다                                   | `step`          |
| 대시보드 연결 없음 | 모든 요구보다 우선해 0/0                                  | `FanLeaseGuard` |

**온도 (`TemperatureController`, `ControlConfig`)**

| 조건                                                                     | 동작                                                             |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| 센서 5초 초과 지연                                                       | 35/35 (`sensor safety`)                                          |
| 온도 ≥ 28°C                                                              | 100/100 (`emergency cooling`)                                    |
| 원본 상승률 보조 활성 + (≥26°C 또는 25.5°C 이상이면서 5초 후 ≥26°C 예상) | 하한 (35,60), 25.5°C 이상이면 (60,100). 이전 PWM보다 낮추지 않음 |
| 26°C 이상이고 상승률 ≥ 0.15°C/s                                          | 100/100                                                          |
| 버퍼 8표본 미만                                                          | 26°C 이상 60/100, 미만 35/35 (`warmup`)                          |
| 25.5°C 이상이고 예측 ≥ 28°C, 또는 26°C 이상이고 상승률 ≥ 0.15            | 100/100                                                          |
| 그 외                                                                    | LSTM v6 예측으로 9개 후보 중 선택                                |

**디바운싱(전환 게이트)**: `TrajectorySwitchGate(.1, 0., 5, 15.)`. 후보 전환은 개선율 10% 초과가 연속 5회 확인되어야 하고, 일반 최소 유지 15초(위험 시 우회)입니다 (`docs/INTEGRATION.md` 기준).

**PM2.5 (`FanController`, `SmokeFan` 기본값: 마감 70초, 범위 50)**

| 규칙           | 값                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------- |
| PM 판단값      | 최근 3표본 중앙값, 500 상한                                                               |
| 범위 안        | PM ≤ 50 이면 35/35                                                                        |
| 센서 무효/지연 | 10초 초과 시 100/100 (`sensor_invalid_or_stale`)                                          |
| 급상승         | 10초 창에서 최소값의 1.3배 이상이고 15 이상 증가 → 가장 빠른 조합, 6초 유지               |
| 측정 상한      | 원시 PM > 500 → 가장 빠른 조합                                                            |
| 계획           | 마감(70초, PM이 500 아래로 내려온 시점부터) 안에서 최소 에너지 조합을 동적계획법으로 선택 |
| 전환 디바운싱  | 조합 변경 후 최소 25초 유지(급상승·상한 초과는 예외), 재계획 주기 1초                     |
| 정체 감지      | PM ≤ 80에서 20초 동안 5% 미만 하락 → 35/35                                                |
| 속도 보정      | 실제/예상 하강 시간 비율 0.7~1.6으로 남은 마감 조정                                       |

### 3-3. 사건(이벤트) 기록 (`PMRecovery` → `EventRecorder`)

| category      | 시작                          | 회복 확정               | 판단값 |
| ------------- | ----------------------------- | ----------------------- | ------ |
| `pm25`        | PM2.5 3표본 중앙값 > 50 µg/m³ | ≤ 50 상태가 3초 유지    | 중앙값 |
| `temperature` | 온도 ≥ 26°C                   | < 26°C 상태가 10초 유지 | 원시값 |

- 표본 간격 2.5초 초과(`stale_seconds`) 또는 센서 오류 시 회복 유지 시간 초기화 (`invalidate`).
- 프로세스가 중단되었다가 재시작하면 이전 진행 중 사건을 `interrupted`로 마감.
- 정상 종료 시 진행 중 사건도 `interrupted`.
- 사진: 시작/진행(10초 간격)/회복 시점, 3초 이내 프레임만, JPEG 헤더·푸터 검증.

### 3-4. 카메라 연기 판정 (`smoke_alert.py` `SmokeJudge`)

| 항목              | 기본값                                                                                                                 |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 판단 주기         | 3프레임마다 1회 (`--stride 3`)                                                                                         |
| 점수 평균         | EMA α = 0.3                                                                                                            |
| 켜기 / 끄기       | 0.50 / 0.30 (v4 코드 기본값). 10/21 당시 장비는 off 0.15 + `--motion-gate`였음. v4 교체 후 실제 운용값은 **확인 불가** |
| 최소 유지         | 2초 (`--hold`)                                                                                                         |
| 해제 알림         | 판정이 꺼진 뒤 4초간 다시 켜지지 않아야 (`--clear-sec`)                                                                |
| motion-gate(선택) | 새 알림 시작 시 최근 3초 안에 프레임 차이 평균 ≥ 4.0 필요                                                              |
| 연속 프레임 조건  | "N프레임 투표" 방식은 없음. EMA + 켜기/끄기 분리 + 유지 시간으로 대체                                                  |

참고: "최근 15프레임 중 10 이상" 같은 프레임 투표 방식은 계획 단계 메모이며 코드에는 구현되어 있지 않습니다.

## 4. 예외 처리

| 상황                             | 동작                                                                                                                                                                                                                                                          |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **센서 Serial 끊김/예외**        | 읽기 스레드가 `serial_ok=False`로 표시 → 이후 `send`가 `ConnectionError` → 통합 루프 예외 → live 모드면 **루프 종료 후 finally에서 0/0 전송**. 자동 재연결은 통합 제어기에는 **없음**(`sensor_bridge.py`의 `read_serial`에는 2초 간격 재연결 있음, 관찰 전용) |
| 첫 DATA 프레임 없음              | 8초 대기 후 `RuntimeError`로 시작 중단                                                                                                                                                                                                                        |
| **센서 값 오류**                 | BME 오류 → 온도 요구 35/35로 대체, 해당 사건 판정 무효. PMS 오류 → PM 모듈은 10초 후 100/100                                                                                                                                                                  |
| 센서 지연(5초 초과)              | `sensorFresh=False`, 온도 `sensor safety` 35/35, 사건 `invalidate`                                                                                                                                                                                            |
| 팬 명령 부분 전송/실패           | `ConnectionError` 발생. `send`가 False를 돌려주면 0/0으로 두고 `enabled=False`                                                                                                                                                                                |
| 대시보드 연결 끊김               | heartbeat 15초 만료 또는 마지막 탭 닫기 → 감시 스레드가 0/0, 0.2초 주기 점검, 2초 간격 재전송                                                                                                                                                                 |
| **추론 실패**(온도 LSTM 등)      | `step`의 예외는 `mode=fault`로 기록. live 모드에서는 **루프 종료 → 0/0**. 별도 CPU 대체 추론 경로는 없음                                                                                                                                                      |
| 정상 종료/SIGTERM/SIGINT         | `stop` 이벤트 → finally에서 `--shutdown` 값(예: 0,0) 전송 후 포트·DB 닫기                                                                                                                                                                                     |
| 강제 종료/전원 차단              | 하드웨어 정지 **보장하지 않음**. ESP32 펌웨어 측 워치독 여부는 **확인 불가**                                                                                                                                                                                  |
| 카메라 프레임 읽기 실패          | `camera_ok=False`, 카메라 해제 후 1초 뒤 재연결 시도 반복(20회마다 로그)                                                                                                                                                                                      |
| 카메라 프레임 지연               | 사건 사진은 3초 초과 프레임 사용 안 함. 대시보드는 지연 시 숨김                                                                                                                                                                                               |
| 알림 서버(POST/WS) 실패          | 별도 스레드, 큐 100개, 실패 시 로그만(판단 루프 영향 없음)                                                                                                                                                                                                    |
| 업로드(Aiven) 실패               | 큐 유지, 성공(commit) 후에만 삭제                                                                                                                                                                                                                             |
| 카메라 서버 프로세스 자체 크래시 | 자동 재시작(systemd 등) **없음**. 부팅 자동 시작도 미구성                                                                                                                                                                                                     |

주의: 통합 제어기에서 추론/센서 예외가 나면 안전하게 정지하지만 **자동 복구하지 않는다**는 점이 현재 한계입니다. 운영에는 서비스 재시작 설정이 추가로 필요합니다.

## 5. 실행 방법

모든 명령은 Jetson에서 실행합니다. 기존 Serial 소유 프로세스를 먼저 종료해야 합니다(USB·카메라는 프로세스 하나만 소유).

### 5-1. 사전 준비

```bash
bash embedded/practice/jetson/jetson_setup.sh     # brltty 제거, dialout, pyserial (재로그인 필요)
ls /dev/serial/by-id/                              # 시리얼 경로 확인
cd AI/Integration
python3 -m pip install -r requirements/control.txt # PyTorch는 JetPack용으로 따로 설치
python3 scripts/check_models.py                    # 모델 해시 검증
```

연기 모델 TensorRT 변환(최초 1회, 약 270초):

```bash
yolo export model=smoke_cls_v4.pt format=engine half=True imgsz=640
```

### 5-2. 통합 제어기 (포트 8095)

```bash
cd AI/Integration
python3 scripts/run_control.py --help
# 관찰 모드(기본, 팬 명령 안 보냄)
python3 scripts/run_control.py --source-api http://127.0.0.1:8094/api/temperature
# 실제 운전
python3 scripts/run_control.py \
  --live --approve-max-policy --shutdown 0,0 \
  --serial-port "$SERIAL_PORT" --host "$CONTROL_HOST" --port 8095 \
  --dashboard-lease-key "$LEASE_KEY_FILE" --lease-timeout 15 \
  --device-id airguard-01 --recovery-camera-url "$CAMERA_ORIGIN"
```

`--live`은 `--approve-max-policy`, `--shutdown`, `--serial-port`가 모두 있어야 시작됩니다. 임대 키를 쓰려면 `--shutdown 0,0`, timeout 5~60초가 필수입니다.

### 5-3. 카메라 연기 서버 (포트 8092)

```bash
python3 embedded/dashboard/integrations/smoke/smoke_alert.py \
  --model /home/jetson/smoke/smoke_cls_v4.engine \
  --source 0 --device 0 --on 0.5 --off 0.30 \
  --host JETSON_IP --port 8092
```

### 5-4. 업로드(별도 프로세스)

```bash
python3 -m pip install -r requirements/upload.txt
python3 scripts/upload_events.py runtime/pm_pending.sqlite3 --env-file runtime/aiven/.env
python3 scripts/upload_events.py runtime/temperature_pending.sqlite3 --env-file runtime/aiven/.env
```

### 5-5. 설정

| 설정           | 위치                                                                                            |
| -------------- | ----------------------------------------------------------------------------------------------- |
| 통합 제어      | 실행 인자 (`run_control.py --help`). 별도 설정 파일 없음                                        |
| 환경변수       | `AIRGUARD_MODEL_DIR`(모델 폴더), `AIRGUARD_RUNTIME_DIR`(로그·DB·사진)                           |
| DB 접속        | `AI/Integration/configs/aiven.env.example`을 `runtime/aiven/.env`로 복사 (키·인증서는 Git 제외) |
| 연기 서버      | 실행 인자 (`--on --off --alpha --hold --stride --rotate --clear-sec --motion-gate`)             |
| 부팅 자동 실행 | 미구성 (systemd 유닛 파일은 저장소에 없음)                                                      |

### 5-6. 결과 파일 (runtime/)

`integrated_events.db`(센서·제어 SQLite), `integrated_decisions.jsonl`(1초당 1줄 결정 로그), `pm_pending.sqlite3`, `temperature_pending.sqlite3`(업로드 큐), `photos/<event_uuid>/<photo_uuid>.jpg`.

### 5-7. API

`GET /api/control`(최신 결정, 5초 초과 시 `stale`), `GET /api/temperature?seconds=&date=`, `GET /api/health`, `GET /api/recovery-photo/<event>/<photo>`, `POST /api/dashboard-lease`(Bearer 키 필요).

## 6. 증거 자료

### 6-1. 확보된 증거

| 증거                                                        | 내용                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 단위·통합 검사                                              | 테스트 39건. 이 문서 작성 환경(클라우드, PyTorch 없음)에서 실행 결과 37건 통과, 2건은 `torch` 미설치로 실패(`test_actual_temperature_v6_api`, `test_project_runs_without_parent_repository`). 코드 오류가 아니라 환경 문제이며 torch 설치 후 재실행은 **미확인** |
| 대시보드 작업 기록                                          | 10/21 Python 회귀 26건 통과. 로컬 최신(10/23): Python 65건, JavaScript 검사 8개, 구문 검사 통과. GitHub 보관본: Python 59건, JavaScript 8개 통과. 모두 모의 센서·임시 DB 기반이며 장비 감지 정확도나 실제 회복 성능을 새로 검증한 것은 아님                      |
| 실장비 확인(10/21)                                          | 최신 센서 API·원본 CCTV·온도 예측·팬 PWM/RPM 수신, 탭 닫기 후 약 4.6초 내 양 팬 0, heartbeat 중단 후 약 18.2초 내 RPM 0, 재접속 후 자동 제어 복구                                                                                                                |
| 연기 추론 성능 (Jetson Orin Nano, 입력 640, 600프레임, 2회) | `.pt` 추론 36.1/35.1ms, 25.2/25.9fps → TensorRT FP16 28.4/27.3ms, 31.4/32.5fps. GPU 메모리 62.7→9.4MB. 전력 6.49→5.66W. 점수 차 최대 0.003, 판정 변경 0                                                                                                          |
| tegrastats 로그                                             | `.pt` 2026-10-21 21:06:36~21:07:52 (151줄), TensorRT 21:10:16~21:11:05 (97줄). 최고 온도 46.7/46.2°C (스로틀링 없는 수준)                                                                                                                                        |
| PM 팬 제어 실측                                             | 자동제어·고정 100/100·고정 35/35 각 3회. 자동제어 PM50 평균 90초, 평균 추정 에너지 103J. 마감 70초 달성 2/3, 1회는 157초 정체                                                                                                                                    |
| PM 제어기 Jetson 실측 로그                                  | 356초 구간, 356행 (1초 간격)                                                                                                                                                                                                                                     |
| 카메라 모델 성능 (`.pt`, test 311장, 임계 0.40)             | 정확도 92.0%(286/311), 재현율 92.7%, 오탐률 8.5%, 정밀도 87.7%, F1 0.901, AUROC 0.977. TP 114 / FN 9 / FP 16 / TN 172. 기준 0.30은 재현율 96.7%·오탐률 11.7%, 0.50은 재현율 87.8%·오탐률 6.4%                                                                    |
| 시연 영상                                                   | 최종 시연 영상(mp4) 있음                                                                                                                                                                                                                                         |
| 화면 캡처                                                   | 대시보드 화면 녹화(.mov 2개)는 있으나, 정지 캡처 이미지는 저장소에 **없음**                                                                                                                                                                                      |
