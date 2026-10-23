# 실제 장비 운전과 업로드

아래 명령은 `AI/Integration`에서 실행한다. 모델과 기본 의존성 준비는 README를 따른다.

## 통합 제어

기존 Serial 소유 실행기를 정상 종료한 뒤 새 실행기를 시작한다.
`SERIAL_PORT`는 장치 by-id 경로, `CONTROL_HOST`는 Jetson 바인딩 주소,
`LEASE_KEY_FILE`은 대시보드 서버와 공유하는 기존 키 파일,
`CAMERA_ORIGIN`은 기존 카메라 서버 주소로 설정한다.

```bash
python3 scripts/run_control.py \
  --live --approve-max-policy --shutdown 0,0 \
  --serial-port "$SERIAL_PORT" --host "$CONTROL_HOST" --port 8095 \
  --dashboard-lease-key "$LEASE_KEY_FILE" --lease-timeout 15 \
  --device-id airguard-01 --recovery-camera-url "$CAMERA_ORIGIN"
```

카메라가 없으면 `--recovery-camera-url`을 생략한다.
이 코드는 영상 서비스와 대시보드 서버를 별도로 시작하지 않는다.
기존 대시보드 화면은 127.0.0.1:8091, 제어 API는 8095를 사용한다.
브라우저 연결 정지를 사용하려면 양 서버에 같은 키를 연결해야 한다.
키 옵션 없이 live를 실행하면 브라우저 연결에 따른 정지를 적용하지 않는다.

마지막 탭 닫기 또는 연결 만료 시 팬 0/0이 우선하며 센서·카메라는 유지한다.
위 명령은 정상 종료 시에도 0/0을 전송한다. 부팅 자동 실행은 구성하지 않는다.
프로세스 강제 종료나 전원 차단 시 하드웨어 정지는 보장하지 않는다.

## 저장 기준

| category | 시작 | 회복 |
| --- | --- | --- |
| `temperature` | 온도 >=26°C | <26°C를 10초 유지 |
| `pm25` | 최대 3표본 PM 중앙값 >50 µg/m³ | <=50 µg/m³를 3초 유지 |

동시 발생은 별도 사건 ID 두 개로 기록한다. `combined`로 새로 합치지 않는다.
실제 센서 시각과 장치 PWM/RPM을 기록한다. 오류·공백은 회복 대기를 초기화한다.
이 기준은 사건 기록용이며 온도 팬 제어 모델이나 별도 열 실험 조건을 대체하지 않는다.

runtime 기본 파일:

- `integrated_events.db`, `integrated_decisions.jsonl`: 센서·제어 기록
- `pm_pending.sqlite3`, `temperature_pending.sqlite3`: 별도 업로드 큐
- `photos/<event_uuid>/<photo_uuid>.jpg`: 실제 사건 사진
- `aiven/.env`, `aiven/ca.pem`: 서버에서 별도 준비할 DB 접속 정보

## Aiven 업로드

```bash
python3 -m pip install -r requirements/upload.txt
python3 scripts/upload_events.py runtime/pm_pending.sqlite3 --env-file runtime/aiven/.env
```

`configs/aiven.env.example`을 복사해 실제 접속 정보를 서버에서 채운다.
CA 상대 경로는 .env 파일이 있는 폴더를 기준으로 한다.
온도 큐는 별도 터미널에서 같은 명령의 경로를
`runtime/temperature_pending.sqlite3`로 바꿔 실행한다.

업로더는 DB commit 성공 후 처리된 큐를 지우고 실패하면 큐를 유지한다.
기존 `e4_recovery_events`, `e4_recovery_samples`, `e4_recovery_photos`를 사용한다.
스키마 생성·ALTER·과거 데이터 삭제는 자동 수행하지 않는다.
사진 원본은 Jetson에 있고 Aiven에는 메타데이터만 저장한다.

## 검증 범위

폴더 이관 검사는 모의 입력과 포함된 모델을 사용한다. 운영 장치·DB에 쓰지 않는다.
기존 서비스는 재시작하지 않았다. 고온·PM 동시 부하의 실제 회복 성능은 별도 평가 대상이다.
