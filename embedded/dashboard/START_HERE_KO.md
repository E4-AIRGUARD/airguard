# AIRGUARD 수정·실행·적용용 공유본

기준: 2026-10-22 최종 수정본. 이 문서가 이전 작업 기록의 중간 상태 설명보다 우선합니다.
ZIP을 새 폴더에 풀어 사용하세요. `dist`는 실제 편집하는 HTML/CSS/JavaScript 소스이며 별도의 npm 빌드가 필요하지 않습니다.

## 1. 화면만 확인하기

Python 3.10 이상을 설치하고 압축을 푼 폴더에서 실행합니다.

```powershell
python -m http.server 8093 --bind 127.0.0.1 --directory dist
```

브라우저: `http://127.0.0.1:8093/?demo=1#dashboard`

데모는 장비와 DB에 연결하지 않습니다. 실측 전용 최근 알림·Aiven 사건·회복 대기 화면은 실제 연결 모드에서 확인합니다. 종료는 터미널에서 Ctrl+C입니다. Windows에서 `python`이 없다면 `py -3`로 바꿉니다.

## 2. 기존 Jetson에 연결하여 실측 실행

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-upload.txt
New-Item -ItemType Directory -Force runtime/aiven
Copy-Item aiven.env.example runtime/aiven/.env
```

`runtime/aiven/.env`에 본인이 사용할 Aiven 주소·사용자·비밀번호를 입력하고, 서비스 CA 인증서를 `runtime/aiven/ca.pem`에 놓습니다. 기존 설정 파일이 있으면 예시로 덮어쓰지 마세요.

아래 `JETSON_IP`를 실제 장비 IP로 바꿉니다. 같은 장비에 네트워크로 접속할 수 있어야 합니다.

```powershell
.\.venv\Scripts\python.exe run.py --control-url http://JETSON_IP:8095
```

브라우저: `http://127.0.0.1:8091/?smoke=http://JETSON_IP:8092&sensors=http://JETSON_IP:8095#dashboard`

- 화면 서버는 PC의 8091, 카메라는 Jetson의 8092, 센서·제어 상태는 Jetson의 8095를 사용합니다.
- 장비 CORS에 맞춰 PC 주소는 `http://127.0.0.1:8091`을 사용합니다.
- 실측 조회만 할 때는 팬 제어 키가 필요하지 않습니다. 자동 운전 권한이 있는 사용자는 운영 담당자에게 동일 장비의 키를 별도로 받아 `runtime/dashboard_lease.key`에 둡니다.
- 유효한 키가 있는 대시보드가 열리면 자동 운전 연결 신호를 보냅니다. 마지막 연결 탭을 닫으면 팬 0/0 정지, 닫기 신호 누락 시 마지막 연결 신호부터 15초 뒤 정지합니다. 다른 연결 탭이 있으면 운전은 유지됩니다.
- 현재 실행 중인 Jetson에 연결만 하는 경우 제어기·카메라·업로더를 추가 실행하지 않습니다.

## 3. 수정할 파일

| 대상 | 파일 |
| --- | --- |
| 공통 레이아웃·스타일 | `dist/index.html`, `dist/styles.css`, `dist/app.js` |
| 메인 실측·트렌드·현재 팬 CFD | `dist/live-measurements.js`, `dist/temperature-integration.js` |
| 최근 알림·전체 페이지 이상 알림 | `dist/recent-alerts.js`, `dist/global-alerts.js` |
| 이상·회복 기록·DB 대기 화면 | `dist/recovery-history.js`, `dist/recovery-history.css`, `dist/recovery-trigger.js` |
| Aiven 조회·CSV·사진 ZIP | `recovery_api.py`, `aiven_connection.py`, `run.py` |
| Jetson 온도·PM 사건 판정 전달 | `integrations/pm_recovery.py`, `integrations/integrated_control.py` |
| 영상 연기 사건 DB 저장 | `integrations/vision_recovery.py` |
| DB 업로더·스키마 | `integrations/smoke_fan_final/uploader.py`, `schema.sql` |

정적 파일 수정 후 브라우저를 강력 새로고침합니다. 필요하면 `dist/index.html`, `dist/dashboard-boot.js`의 해당 파일 `?v=` 값을 올립니다. Python 서버 코드 수정은 서버를 종료한 뒤 다시 실행합니다. 자신의 변경본 폴더에서 실행해야 반영됩니다.

## 4. 기존 Jetson에 제어 코드 적용

먼저 현재 실행 명령과 설치 경로를 확인합니다. 통합 제어기와 USB 포트는 한 프로세스만 소유해야 합니다.

1. 현재 제어기 파일을 백업합니다. 진행 중인 PM·온도 사건이 끝난 뒤 정상 종료합니다. 사건 진행 중 종료하면 `interrupted`로 저장됩니다.
2. 이 공유본의 `integrations/pm_recovery.py`, `integrations/integrated_control.py`, `integrations/vision_recovery.py`를 기존 Jetson 제어기 폴더에 함께 복사합니다. 나머지 코드도 수정했다면 해당 의존 파일을 함께 반영합니다.
3. 기존 `runtime/`, 모델, DB 대기열, 사진, 키를 유지합니다. 공유본으로 운영 디렉터리를 통째로 지우거나 덮어쓰지 않습니다.
4. 기존 실행 인수를 그대로 사용해 제어기를 다시 실행합니다. 영상 사건·사진 연동에는 `--recovery-camera-url http://JETSON_IP:8092`가 필요합니다. `--device-id`는 기존 장비 ID를 유지합니다.
5. PM/영상 공용 `pm_pending.sqlite3`와 온도 `temperature_pending.sqlite3`의 업로더를 각각 한 개씩 유지합니다. 영상 전용 업로더를 별도로 추가하지 않습니다.
6. `/api/temperature` 응답의 `integratedControl.recoveryEvents`, 화면 수신 상태와 Aiven 사건 목록을 확인합니다.

현재 운영 폴더는 `/home/jetson/smoke/control_runtime_20261021`입니다. 다른 설치에서는 실제 경로로 바꿉니다. 처음 설치하는 경우 `README.md`의 Jetson 설치 절차와 `docs/AIVEN_INTEGRATION_20261021.md`를 함께 참고합니다.

업로더 실행 예시(공유본 디렉터리 구조 기준):

```bash
python3 integrations/smoke_fan_final/uploader.py integrations/runtime/pm_pending.sqlite3 --env-file /private/aiven.env
python3 integrations/smoke_fan_final/uploader.py integrations/runtime/temperature_pending.sqlite3 --env-file /private/aiven.env
```

각 명령은 별도 터미널 또는 기존 서비스 관리 방식으로 실행합니다. 이미 실행 중인 업로더가 있으면 중복 실행하지 않습니다. SQLite의 `recovery_status`, `vision_status`는 자동 생성하며 Aiven 스키마 변경은 필요하지 않습니다.

## 5. 이번 버전의 동작

- Jetson의 실제 사건 UUID와 회복 확정 신호를 받는 즉시 목록을 ‘회복 완료’로 표시합니다. 센서가 잠깐 정상으로 돌아온 것만으로 완료 처리하지 않습니다.
- 선택한 사건이 DB에서 아직 진행 중이면 ‘불러오는 중’ 화면으로 기다립니다. 이전 요청 응답 후 1초 뒤 추가 조회하며 `completed` 응답에서 상세를 표시하고 재시도를 멈춥니다. 중단 사건은 회복 완료로 바꾸지 않습니다.
- 영상 연기 감지·해제는 기존 카메라 서버의 판정을 저장합니다. PM 사건과 별도인 ‘영상 연기’로 분류하며 팬 제어 입력을 바꾸지 않습니다. 원래 감지 사진도 저장합니다. 카메라 서버에 남아 있는 과거 사건은 중복 없이 보완 저장합니다.
- 과거 영상 사건의 센서 표본을 만들어 넣지 않습니다. 영상 사건은 감지/해제 시간과 사진 중심으로 표시합니다.
- 메인 ‘최근 알림’에 온도 이상·회복, PM 이상·회복, 영상 연기 감지·해제를 시간순으로 최대 40개 표시합니다.
- 실측 토스트/떠 있는 연기 팝업, 메인 통합 팬 제어 설명 박스, 현재 팬 CFD 영상 아래의 파일명·설명을 제거했습니다. 공통 이상 알림은 유지합니다.

## 6. 검증

Node.js가 설치되어 있으면:

```powershell
node tests/recovery-live.cjs
node tests/recovery-history.cjs
node tests/recovery-trigger.cjs
node tests/recent-alerts.cjs
node tests/dashboard.cjs
node tests/smoke-camera.cjs
```

```powershell
.\.venv\Scripts\python.exe tests/pm_recovery.py
.\.venv\Scripts\python.exe tests/vision_recovery.py
.\.venv\Scripts\python.exe tests/recovery_api.py
```

전체 Python 제어 테스트(`tests/run_all.py`)에는 `requirements-control.txt` 및 환경에 맞는 PyTorch가 필요합니다. Jetson에는 기존 JetPack과 맞는 환경을 사용합니다.

## 7. 포함·제외 항목

포함: 수정 가능한 소스, CFD 영상 27개와 자산, 온도 LSTM 모델, 테스트, 스키마, 접속 설정 예시, 설치/적용 안내.

제외: 실제 `.env`, DB 비밀번호, CA/개인 키, 팬 제어 인증 키, 가상환경, 실행 로그, SQLite DB, 개인 실험 사진·CSV, Git 이력. 새 사용자에게 필요한 접속 권한과 설정은 별도로 제공해야 합니다.

카메라 감지 Python 소스는 포함하지만 현재 장비의 `smoke_cls_v7_640.engine`은 포함하지 않습니다. 기존 Jetson의 카메라 서비스를 사용하거나 대상 Jetson에 맞는 검증된 연기 모델을 따로 준비해야 합니다. 이 ZIP의 카메라 소스는 저장소 전달본이며 실행 중인 카메라를 이번 작업에서 교체하지 않았습니다.

`MANIFEST_SHA256.json`에 ZIP 안 파일들의 SHA-256이 들어 있습니다. 소스를 수정하면 해당 파일 해시는 달라집니다.
