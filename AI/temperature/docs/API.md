# 온도 알림·회복 기록 API

temperature 폴더에서 `python3 src/temperature_alert.py`로 실행합니다.
기본 바인딩은 127.0.0.1:8094이며 제어기가 기록하는 `runtime/edge_events.db`를 읽습니다.
외부 공개용 인증·TLS 서버는 아닙니다. 포트 공개 전에 인증·네트워크 접근 제한이 필요합니다.

| 경로 | 제공값 |
|---|---|
| GET /api/temperature | 현재 센서·예측·제어·이벤트·최근 명령 기록 |
| GET /api/snapshot | 위와 동일한 스냅샷 |
| GET /api/events?seconds=86400 | 최근 24시간 센서에서 파생한 사건 목록 |
| GET /api/events?seconds=86400&date=2026-10-23 | 조회 창 안에서 KST 발생 날짜 필터 |

seconds는 1~86400이며 기본 300입니다. 과거 날짜 조회는 현재 시각 기준 조회 창에
포함된 사건만 제공합니다. 날짜 전체의 영구 아카이브 API가 아닙니다.
행 수 상한 50,000 초과 시 historyTruncated=true입니다. 조회 창 시작 전에 발생한
사건은 시작·지속시간이 잘릴 수 있으므로 완전한 사건 기록으로 해석하지 않습니다.
현재 사건은 SQLite 센서 기록으로 조회 시 재구성하며 별도 사건 테이블에 쓰지 않습니다.

## 화면 연결

약 1초 간격으로 GET /api/temperature를 호출합니다. HTML 레이아웃 변경 없이 값과
상태 클래스만 연결합니다. 기존 프런트의 필드명이 다르면 아래 필드에 매핑해야 합니다.

| 필드 | 사용 |
|---|---|
| sensor.temperature_c / humidity_pct / pm25_ug_m3 | 현재 센서 표시 |
| predictedTemperatureC | 최근 제어기 예측. 제어값이 5초 넘게 오래되면 null |
| control.candidates | 9개 후보 상세. 데이터가 없으면 빈 배열 |
| control.intake_percent / exhaust_percent | 목표 PWM |
| sensor.intake_pwm / exhaust_pwm / intake_rpm / exhaust_rpm | 실제 피드백 |
| state | normal / abnormal / unknown |
| abnormal | fresh 사건 진행 중일 때 true. 컨테이너 상태 빨간 표시 조건 |
| stale / ageSec | 수신 지연. unknown을 정상으로 표시하지 않음 |
| events | 최근 이상·회복 목록 및 회복 추이 history |
| recentRecords | 최근 제어 명령 변경 기록. 감지 알림은 events와 함께 표시 |
| estimatedPowerW / energy | 실측 RPM 기반 추정 전력·조회 구간 에너지 |

알림 팝업은 active 사건의 id가 새로 등장할 때 한 번만 표시하고 id를 기억합니다.
같은 사건을 매 폴링마다 새 알림으로 띄우지 않습니다. API 오류·stale에서는
‘연결 확인 필요’를 표시하고 최신 이상 여부를 확정하지 않습니다.

## 알림 기준

- 원본 온도 ≥26°C 또는 현재 ≥24.5°C에서 직전 표본 대비 상승률 ≥0.08°C/s면 사건 시작.
- 원본 온도 <26°C이고 상승률 <0.02°C/s인 연속 3표본·2초 이상이면 회복.
- 2.5초 초과 표본 공백·결측은 interrupted이며 회복으로 세지 않음.
- 마지막 센서 5초 초과 지연은 state=unknown, stale=true.
- events의 status는 active / recovered / interrupted. trigger는 temperature_limit / temperature_rise.

이는 대시보드 표시용 규칙이며 학습 모델 판정이나 제어기의 원본 상승률 보조와
동일한 알고리즘이 아닙니다. 관측 상승 경보가 발생해도 26°C 초과 사건은 아닐 수 있습니다.
회복시간과 에너지 역시 조회 창의 관측값이며 모델의 예상 회복시간과 구분합니다.

## 경계

DB가 없어도 빈 상태를 반환하고 새 DB를 만들지 않습니다. DB 잠금·스키마 오류는
stale/unknown 상태로 반환합니다. JSON의 결측·비정상 수는 null로 반환합니다.
POST는 405이며 팬 제어 endpoint와 파일 제공 기능은 없습니다.
--allow-origin에는 실제 대시보드 origin을 지정합니다. localhost와 127.0.0.1은 다릅니다.
기존 삭제된 API 원본을 그대로 복원한 버전이 아니라 현재 스키마에 맞춘 재작성본입니다.
