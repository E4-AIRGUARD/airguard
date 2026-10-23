# 온도 API 전달 안내 — HTML 레이아웃 변경 없음

## 전달 파일

`src/temperature_alert.py`, `src/dashboard_api.py`, `src/temperature_events.py`, `src/fan_power.py` 네 파일을 같은 폴더에 전달합니다. Python 표준 라이브러리만 사용하며 torch/카메라/USB를 열지 않습니다. 전체 제어 실행 파일은 기존 handoff_v6 묶음에 있습니다.

팀원의 `smoke_alert.py`는 그대로 실행합니다. `realtime_demo_cls.py`는 영상 데모이며 온도 API 의존성이 아닙니다. **팀원의 `dashboard_sensor_bridge.py`는 main_runner와 동시에 실행하지 않습니다.** 두 프로그램이 같은 USB 포트를 열면 수신 충돌이 발생합니다. 온도 API는 해당 브리지와 같은 8094 포트 및 `/api/temperature` 경로를 사용합니다.

## 실행

handoff_v6 폴더에서 터미널 두 개를 사용합니다.

```bash
python3 src/main_runner.py --serial-port /dev/ttyUSB0 --database runtime/edge_events.db
python3 src/temperature_alert.py --database runtime/edge_events.db --port 8094 --allow-origin http://127.0.0.1:8091
```

main_runner는 센서를 받을 때마다 자동 저장하고 제어 결과도 저장합니다. 두 명령의 DB는 반드시 동일한 파일이어야 합니다. 다른 main_runner를 사용하는 경우 그 프로그램이 실제 쓰는 DB 경로를 지정합니다. 5초 예측 v6 제어기용 계약이며, 예전 10초 모델 DB를 연결하면 안 됩니다.

대시보드가 localhost:8091이면 allow-origin도 `http://localhost:8091`로 일치시킵니다. 원격 브라우저는 127.0.0.1이 Jetson이 아닌 브라우저 PC를 가리킵니다. SSH 포트 포워딩 또는 대시보드 서버의 동일 출처 프록시를 사용하세요. 공개 인터넷에 인증 없이 노출하지 않습니다.

## 기존 화면에 값만 연결

1초마다 GET `http://127.0.0.1:8094/api/temperature?seconds=300` 조회합니다. 과거 사건은 `&date=2026-10-21`로 조회합니다. HTML/CSS/카드 배치를 바꿀 필요 없습니다.

| 기존 표시 위치 | JSON 값 |
|---|---|
| 현재 온도 / 습도 / PM2.5 | `sensor.temperature_c`, `sensor.humidity_pct`, `sensor.pm25_ug_m3` |
| 예측 온도(기존 슬롯이 있을 때) | `predictedTemperatureC`, `forecastHorizonSec` |
| 컨테이너 상태 | `stale`이면 수신 지연, 아니면 `abnormal`일 때 빨간 **이상**, 나머지 정상 |
| 목표 팬 PWM | `control.intake_percent`, `control.exhaust_percent` |
| 실제 장치 PWM / RPM | `sensor.intake_pwm`, `sensor.exhaust_pwm`, `sensor.intake_rpm`, `sensor.exhaust_rpm` |
| 제어 이유 / 회복 예상 초 | `control.reason`, `control.recovery_seconds` |
| 센서 그래프 | `history`의 timestamp와 센서 필드 |
| 최근 알림 / 이상·회복 기록 | `incidents` (최신순); start는 감지, end는 회복 |
| 진행 중 사건 | `current`; 없음은 null |
| 명령 변경 기록 | `controlEvents` |
| 순간 추정 전력 / 구간 추정 에너지 | `estimatedPowerW`(W), `energy.energy_wh`(Wh) |

`null`은 `—`로 표시하며 0으로 바꾸지 않습니다. `stale=true` 또는 HTTP 오류 때 정상/회복으로 표시하면 안 됩니다. `serialConnected`는 직접 포트 상태가 아니라 **10초 이내 DB 수신 여부**입니다. `controlStale=true`면 오래된 예측/목표 명령을 현재 값처럼 표시하지 않습니다. 센서 피드백과 목표 PWM은 다른 값입니다. dry-run의 `control.command_sent=0`이면 실제 전송이 아닙니다.

연기와 통합한 컨테이너 이상은 신선한 온도 `abnormal` 또는 신선한 연기 `smoke`가 참일 때입니다. 연기 API의 cameraOk/ageSec도 확인해야 합니다. 이 API는 팬 명령을 받지 않습니다.

## 이벤트 의미와 제한

- 실제 온도 >=26°C 또는 온도 상승 규칙을 2회 연속 만족하면 사건 시작.
- 상승 규칙: T>=24.5°C, 1초 상승률>=0.08°C/s, 3초 상승률>=0.04°C/s, T+5×3초 상승률>=25.5°C. 화면 이벤트용 규칙이며 LSTM 제어 정책을 변경하지 않습니다.
- T<26°C, 1·3초 상승률<0.02°C/s가 3표본·2초 이상 지속하면 회복.
- 센서 공백>2.5초는 interrupted, 수신 지연 상태의 미완료 사건은 unconfirmed. 회복으로 간주하지 않습니다.
- 사건은 SQLite 원본 센서에서 재구성되므로 API 재시작 후에도 조회됩니다. 별도 알람 테이블을 기록하는 프로그램은 아닙니다. 날짜 경계 이전에 시작한 사건은 전체 시작점을 알 수 없습니다. 날짜별 사건 조회는 최대 5초 캐시입니다.
- 최신 제어 예측 >=26°C도 현재 이상 표시에 반영하지만, **예측 위험만 있고 센서 상승이 없는 경우 incidents에는 추가하지 않습니다.** 센서 사건과 모델 경보를 구분합니다.
- 에너지는 실제 RPM 기반 상사법칙 추정치이며 실측 전력이 아닙니다. 결측/공백은 제외됩니다.

프런트엔드 담당자는 기존 JS 데이터 바인딩에서 위 필드만 매핑하면 됩니다. 기존 HTML이나 화면 렌더 함수를 교체하는 파일은 포함하지 않았습니다.
