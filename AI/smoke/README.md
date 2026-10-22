# 온도·PM 팬 자동제어 통합 코드

실행 파일은 `AI/smoke/integrated_control.py`다. 2026-10-21 대시보드에서
검증한 통합 코드를 이 폴더에서 실행하도록 구성했다. 온도와 PM의 요구 중
흡기·배기 각각의 최대 PWM을 사용하며 하나의 실행기만 Serial을 소유한다.
카메라의 연기 확률은 팬 제어 입력이 아니다.

## 구성과 의존 파일

| 파일 | 역할 |
| --- | --- |
| `integrated_control.py` | 센서 수신, 온도·PM 중재, 팬 명령, 조회 API |
| `smoke_fan.py` | PM 제어기 연결 및 PM 요구값 계산 |
| `pm_recovery.py`, `event_recorder.py` | 온도·PM 사건의 독립 SQLite 큐 |
| `recovery_camera.py`, `event_photos.py` | 기존 영상 API의 최신 프레임 재사용·사진 기록 |
| `uploader.py` | 별도 프로세스에서 사건·표본·사진 메타데이터 업로드 |
| `aiven_connection.py` | 서버 전용 설정과 CA를 사용하는 TLS DB 연결 |
| `tests/` | 장비·운영 DB에 쓰지 않는 회귀 검사 |

**저장소 전체를 clone해서 사용한다.** 이 폴더만 복사하면 공통 의존 파일이 없다.
기존 `embedded/dashboard/integrations`의 `dashboard_lease.py`,
`sensor_bridge.py`, `integration_store.py`, `handoff_v6/src`,
`smoke_fan_final/fan_controller.py`, `fan_model.py`를 재사용한다.
온도 모델과 PM 모델도 기존 저장소 경로를 사용하므로 새로 커밋하지 않는다.

기본 모델 경로:

- `embedded/dashboard/integrations/handoff_v6/models/lstm_temperature_trajectory_v6.pt`
- `embedded/dashboard/integrations/smoke_fan_final/model.json`

모델은 `--temperature-model`, `--smoke-model`로 지정할 수 있다.
원래 통합 정책은 유지했고, 관찰 모드의 기본 센서 주소는 localhost로 바꿨다.
원격 센서는 `--source-api`로 명시한다. 원래 대시보드 소스는 수정하지 않았다.

## 환경과 실행

Python 3.10 및 JetPack/CUDA에 맞는 PyTorch를 먼저 준비한다.
아래 명령은 저장소 루트에서 실행한다. 기존 의존성 버전 범위를 유지한다.

```bash
python3 -m pip install -r embedded/dashboard/requirements-control.txt
python3 AI/smoke/integrated_control.py --help
```

기본은 팬 명령을 보내지 않는 관찰 모드다. 실제 센서 API 주소를 지정한다.

```bash
python3 AI/smoke/integrated_control.py \
  --source-api http://127.0.0.1:8094/api/temperature
```

실제 제어는 기존 Serial 소유 실행기를 정상 종료한 뒤 시작한다.
`SERIAL_PORT`는 장치의 by-id 경로, `CONTROL_HOST`는 Jetson의 바인딩 주소,
`LEASE_KEY_FILE`은 대시보드 서버와 공유하는 기존 키 파일,
`CAMERA_ORIGIN`은 기존 카메라 서버 주소로 설정한다.

```bash
python3 AI/smoke/integrated_control.py \
  --live --approve-max-policy --shutdown 0,0 \
  --serial-port "$SERIAL_PORT" --host "$CONTROL_HOST" --port 8095 \
  --dashboard-lease-key "$LEASE_KEY_FILE" --lease-timeout 15 \
  --device-id airguard-01 --recovery-camera-url "$CAMERA_ORIGIN"
```

카메라를 사용하지 않으면 `--recovery-camera-url`을 생략한다.
이 실행기는 카메라 프로세스나 대시보드 서버를 대신 시작하지 않는다.
기존 대시보드의 제어 URL은 8095, 화면 서버는 `http://127.0.0.1:8091`이다.
브라우저 연결 제어를 사용하려면 양 서버가 동일한 기존 키를 읽어야 한다.
키 없이 live를 실행하면 브라우저 연결 여부에 따른 정지가 적용되지 않으므로
대시보드 연동 운전에는 위 키 옵션을 포함한다.

마지막 탭 연결이 사라지면 팬 0/0을 적용하고 센서·카메라는 유지한다.
정상 종료 시에도 위 명령의 `--shutdown 0,0`을 적용한다.
부팅 자동 실행은 구성하지 않으며 강제 종료·전원 차단 시 정지를 보장하지 않는다.

## 사건 저장과 업로드

| category | 시작 | 회복 |
| --- | --- | --- |
| `temperature` | 온도 >=26°C | <26°C를 10초 유지 |
| `pm25` | 최대 3표본 중앙값 >50 µg/m³ | <=50 µg/m³를 3초 유지 |

동시에 발생하면 두 category와 두 사건 ID로 각각 저장한다.
`combined`로 합쳐 저장하지 않는다. 사건 기준은 온도 팬 제어 모델과 별개다.
실제 센서 시각과 장치 PWM/RPM을 기록하고 센서 공백·오류는 회복 대기를 초기화한다.

기본 결과 경로는 모두 이 폴더의 `runtime/` 아래다.

- `integrated_events.db`: 공통 센서·제어 기록
- `integrated_decisions.jsonl`: 제어 결정 로그
- `pm_pending.sqlite3`, `temperature_pending.sqlite3`: 독립 업로드 큐
- `photos/<event_uuid>/<photo_uuid>.jpg`: 사건 사진 원본

이미 구성된 Aiven DB에 업로드할 때만 아래 별도 프로세스를 실행한다.
`AIVEN_ENV_FILE`은 기존 서버 전용 설정 파일이다. 설정값·인증 파일은 커밋하지 않는다.

```bash
python3 -m pip install -r AI/smoke/requirements-upload.txt
python3 AI/smoke/uploader.py AI/smoke/runtime/pm_pending.sqlite3 --env-file "$AIVEN_ENV_FILE"
```

온도 큐는 다른 터미널/서비스에서 같은 명령의 큐 경로를
`AI/smoke/runtime/temperature_pending.sqlite3`로 바꿔 실행한다.
업로더는 DB commit 성공 후 처리된 로컬 큐를 지우고 실패 시 보존한다.
대상은 기존 `e4_recovery_events`, `e4_recovery_samples`, `e4_recovery_photos`다.
운영 DB 초기화나 ALTER는 자동 수행하지 않는다. 사진 원본은 Jetson에 남고
DB에는 메타데이터만 저장한다.

## 검증

```bash
python3 -m unittest discover -s AI/smoke/tests -p 'test_*.py' -v
```

검사는 팬별 최대값, 명령 성공 후 확인, 센서 지연·중복, 탭 연결 정지,
실제 RPM 에너지 적분, 온도·PM 독립 사건, 업로드 실패 시 큐 보존과 사진 공백을 다룬다.
현재 장비의 실행 파일은 기존 배포 위치에 유지된다. 이 커밋은 장비 서비스를
재시작하거나 새 경로로 전환하지 않는다. 실제 동시 부하의 회복 성능은 별도 평가 대상이다.
