# AIRGUARD 실측 대시보드

Windows PC에서 실행하는 로컬 대시보드와 Jetson 센서·팬 통합 코드입니다. `main` 병합은 공개 사이트 배포를 수행하지 않습니다.

## 팀원 빠른 실행

Git, Python 3.10 이상이 필요합니다. 화면 서버는 Python 표준 라이브러리만 사용합니다. 팀 저장소와 Jetson의 Tailscale 네트워크에 접근할 권한이 있어야 실제 장비를 조회할 수 있습니다.

```powershell
git clone https://github.com/E4-AIRGUARD/airguard.git
cd airguard/embedded/dashboard
python run.py
```

브라우저에서 다음 주소를 엽니다.

`http://127.0.0.1:8091/?smoke=http://100.106.99.75:8092#dashboard`

- 연기·CCTV: Jetson 8092, 센서·통합 제어 상태: 같은 호스트의 8095.
- 새로 받은 저장소에는 인증 키가 없습니다. 키 없이도 실측 조회가 가능하지만 해당 PC가 팬 자동 운전을 활성화하지는 못합니다. 연결 실패는 미수신/빈 값으로 표시합니다.
- 자동 제어 권한이 있는 팀원은 운영 담당자에게 같은 장비의 키를 안전한 경로로 받아 `runtime/dashboard_lease.key`에 저장합니다. 키를 Git·채팅·정적 파일에 넣지 않습니다.
- 키가 있는 PC에서 화면을 열면 자동 운전합니다. 마지막 연결 탭을 닫으면 0/0 정지, 닫기 통신이 누락되면 마지막 heartbeat로부터 15초 뒤 정지합니다. 다른 PC/탭이 계속 연결되어 있으면 운전을 유지합니다. 센서와 카메라는 계속 실행됩니다.
- 현재 Jetson 서비스가 이미 실행 중이면 팀원마다 원격 제어기를 추가 실행하지 않습니다. USB와 카메라는 각각 한 프로세스만 소유해야 합니다.

주소나 키 위치가 다른 환경에서는 다음처럼 설정합니다. `--control-url`은 팬 연결 신호의 목적지이며, 화면 조회 주소도 함께 변경해야 합니다.

```powershell
python run.py --control-url http://JETSON_IP:8095 --lease-key C:/private/dashboard_lease.key
```

화면: `http://127.0.0.1:8091/?smoke=http://JETSON_IP:8092&sensors=http://JETSON_IP:8095#dashboard`

Windows에서 `python`을 찾지 못하면 `py -3`를 사용합니다. 현재 장비의 센서 CORS는 `http://127.0.0.1:8091`에 맞춰져 있으므로 실측 시연은 이 주소를 사용합니다. `--port 8093`은 오프라인 화면 확인에는 사용할 수 있지만 장비 API의 허용 출처도 변경하지 않으면 센서 조회는 차단됩니다. 화면 서버 종료는 Ctrl+C입니다.

## Jetson 최초 구성 또는 재설치

기존 서비스의 교체·재시작이 필요한 운영 담당자만 수행합니다. Jetson에서 저장소를 복제하고 `embedded/dashboard`로 이동합니다. 기존 USB 수신기/팬 제어기를 먼저 정상 종료하고 중복 실행하지 않습니다.

1. JetPack에 맞는 PyTorch와 카메라 추론용 Ultralytics·OpenCV·TensorRT 환경을 준비합니다. CPU용 PyTorch를 Jetson GPU 환경 위에 설치하지 않습니다. 센서 통합 의존성은 `python3 -m pip install -r requirements-control.txt`입니다.
2. 실제 시리얼 경로를 `ls /dev/serial/by-id/`로 확인합니다. 지원 펌웨어는 115200 baud의 `DATA,...` 및 `F 흡기 배기`/`0` 프로토콜입니다. T08의 JSON/`C` 프로토콜과 다릅니다.
3. 첫 설치 때만 키를 생성하고 권한을 제한합니다. 기존 PC가 연결 중인 키는 임의로 다시 만들지 않습니다.

```bash
mkdir -p integrations/runtime
(umask 077; python3 -c "import secrets; from pathlib import Path; p=Path('integrations/runtime/dashboard_lease.key'); f=p.open('x'); f.write(secrets.token_hex(32)); f.close()")
python3 integrations/integrated_control.py \
  --live --approve-max-policy --shutdown 0,0 \
  --serial-port /dev/serial/by-id/DEVICE_ID \
  --host JETSON_IP --port 8095 \
  --dashboard-lease-key integrations/runtime/dashboard_lease.key --lease-timeout 15
```

`DEVICE_ID`와 `JETSON_IP`를 실제 값으로 바꿉니다. 두 번째 터미널에서 카메라 서버를 실행합니다. 연기 모델은 저장소에 포함되지 않으며 해당 Jetson에서 검증된 파일이 필요합니다. 기존 시연의 v7 엔진은 새 장비에서 재생성/검증이 필요할 수 있습니다.

```bash
python3 integrations/smoke/smoke_alert.py \
  --model /home/jetson/smoke/smoke_cls_v7_640.engine \
  --source 0 --device 0 --on 0.5 --off 0.15 --motion-gate \
  --host JETSON_IP --port 8092
```

현재 저장소의 연기 소스는 v4 ZIP 반영본으로 기본 `off=0.30`입니다. 위 명령은 현재 장비 설정과 맞추기 위해 `--off 0.15`를 명시합니다. 10/21 실제 장비는 이전 motion-gate 소스를 계속 실행 중이며 v4 소스 원격 재배포는 하지 않았습니다. 신규 소스의 실장비 재검증은 별도 필요합니다.

Windows PC로 키를 받을 때(개별 팀원의 권한이 있는 경우):

```powershell
New-Item -ItemType Directory -Force runtime
scp jetson@JETSON_IP:~/airguard/embedded/dashboard/integrations/runtime/dashboard_lease.key runtime/dashboard_lease.key
python run.py --control-url http://JETSON_IP:8095
```

원격 복제 위치가 다르면 SCP 경로도 변경합니다. 현재 운영 장비의 키는 `~/smoke/control_runtime_20261021/runtime/dashboard_lease.key`에 있습니다. 부팅 자동 시작은 구성하지 않았습니다.

## 제어 규칙과 데이터 의미

온도 v6 제어기의 5초 예측 요구와 PMS5003의 원시 PM2.5 기반 요구를 각각 계산하고 **흡기·배기별 최대값**을 적용합니다. 예: 온도 60/100, PM 100/60이면 최종 100/100입니다. 카메라 연기 확률은 사건/영상 표시용이며 팬 입력이 아닙니다. 대시보드 연결이 없으면 모든 요구보다 0/0 정지가 우선합니다.

정상 기본 요구는 35/35입니다. PM 수신 지연이 10초를 넘거나 초기 유효 PM이 없으면 PM 모듈이 100/100을 요구할 수 있습니다. 온도 유효 표본이 없으면 온도 요구는 35/35입니다. 실제 장치 PWM/RPM, 각 제어기의 요구, 최종 전송값을 구분해 표시합니다.

에너지는 실제 RPM 기반 추정 전력을 한 번만 적분합니다. 서로 다른 온도/PM 모듈의 에너지 수치를 합산하지 않습니다. 2.5초를 넘는 표본 간격은 적분에서 제외합니다. 전력계를 통한 실측 전력은 아닙니다.

연기 사건은 서버 메모리에 최대 30건, 온도 사건과 센서 기록은 SQLite에 저장됩니다. 센서 오류나 통신 공백을 회복으로 기록하지 않습니다. CCTV는 `/frame.jpg`를 최대 초당 4회 요청하며 지연 시 예시 사진으로 대체하지 않습니다. 하드웨어 제품 사진과 CFD 선택 영상은 측정값과 구분됩니다.

## 수정할 파일

| 위치 | 역할 |
| --- | --- |
| `dist/index.html`, `dist/styles.css`, `dist/app.js` | 화면 구조·스타일·기본 라우팅 |
| `dist/device-link.js`, `dist/live-measurements.js` | 센서 조회·실측 표시 |
| `dist/temperature-integration.js` | 온도 예측·통합 팬 목표·온도 사건 |
| `dist/live-camera.js`, `dist/smoke-alert.js` | 실시간 영상·연기 사건 |
| `dist/dashboard-lifecycle.js` | 탭 연결 신호·닫기 통지 |
| `run.py` | 로컬 서버·읽기 전용 DB·인증 프록시 |
| `integrations/integrated_control.py` | 센서/팬 독점 연결·요구 통합 |
| `integrations/dashboard_lease.py` | 연결 만료·독립 팬 정지 감시 |
| `integrations/integration_store.py` | 센서·제어·사건 영속 저장 |
| `integrations/handoff_v6/`, `integrations/smoke_fan_final/` | 전달받은 모델·제어기·참고 자료 |

전달본 내부 README와 가상 시험 자료는 원본 참고 자료입니다. 현재 운영 방법은 이 문서와 [통합 기준](integrations/INTEGRATION.md)을 따릅니다. 모델 파일은 온도 v6만 포함합니다. 개인 키·측정 DB·실행 로그·PID는 Git에서 제외합니다.

## 검사와 공동 수정

검사에는 Node.js 22, Python 3.10과 CPU PyTorch가 필요합니다. 개발 PC의 별도 가상환경에서 설치합니다.

```powershell
python -m pip install -r requirements-control.txt
python -m pip install "torch>=2.5,<3" --index-url https://download.pytorch.org/whl/cpu
node tests/dashboard.cjs
python tests/run_all.py
```

검사는 모의 센서/시리얼·임시 DB·로컬 HTTP 서버와 포함된 온도 모델을 사용하며 장비에 명령을 보내지 않습니다. GitHub Actions에서도 JS 문법 및 같은 검사를 실행합니다. 실제 센서·영상·팬과 화면 배치는 별도로 확인해야 합니다.

저장소 루트에서 최신 main을 받고 작업별 브랜치를 만듭니다. 수정 후 검사하고 필요한 파일만 커밋하여 PR을 만듭니다. 자세한 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)입니다.

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/dashboard-next-change
# 수정 및 검사 후 실제 변경 파일만 git add
git commit -m "Describe dashboard change"
git push -u origin feature/dashboard-next-change
```

이번 통합의 적용 이력과 실장비 검증은 [2026-10-21 작업 기록](docs/WORK_LOG_20261021.md), 현재 기준은 [DASHBOARD_SSOT.md](docs/DASHBOARD_SSOT.md)에 있습니다. 제품 이미지 출처는 `dist/assets/hardware/sources.json`을 유지합니다.
