# 대시보드·센서·팬 통합 기준

현재 기준은 [대시보드 SSOT](../docs/DASHBOARD_SSOT.md)입니다. 최초 실행은 [README](../README.md), 날짜별 적용 이력은 [작업 기록](../docs/WORK_LOG_20261021.md)을 확인합니다.

## 흐름

브라우저 → 로컬 `run.py:8091` → 인증된 연결 신호 → Jetson 통합 제어기 `:8095`.
브라우저는 Jetson `:8095/api/temperature`, `/api/control`과 연기 서버 `:8092/api/smoke`, `/frame.jpg`를 조회합니다.

`integrated_control.py` 하나가 ESP32 Serial을 소유하고 센서 수신과 팬 명령을 처리합니다. `sensor_bridge.py`, `handoff_v6/src/main_runner.py`, PM 단독 실행기를 동시에 같은 장치에 연결하면 안 됩니다. 조회 전용 `--source-api` shadow 모드는 외부 센서 API를 읽고 제안만 계산합니다.

## 데이터 계약

- `control`: 온도 단독 요구. `demand_only=1`, `command_sent=0`.
- `integratedControl.proposed`: 통합 목표(연결이 없으면 0/0).
- `integratedControl.lastSent`: 마지막 전송 명령. 전송 성공은 장치 도달을 뜻하지 않습니다.
- `sensor.*_pwm`, `sensor.*_rpm`: 장치 피드백.
- `mode`: `live`, `paused`, `shadow`, `fault` 상태. 화면은 통신 지연도 별도로 표시합니다.
- 유효한 온도 표본만 모델에 입력하며 준비 단계의 현재 온도를 예측값으로 표시하지 않습니다.
- 실제 RPM 기반 에너지는 한 번만 적분하며 PM 가상 요구 에너지는 별도 항목입니다.

## 팬 권한

`--live --approve-max-policy --shutdown 0,0 --dashboard-lease-key KEY_PATH`로 시작해야 브라우저 연결과 연동됩니다. `--dashboard-lease-key`를 빼고 live로 실행하면 브라우저 연결과 무관하게 제어하므로 팀 시연에서는 빼지 않습니다. 키는 로컬 서버에만 저장하고 브라우저가 전송하는 JSON에는 넣지 않습니다.

탭마다 3초 heartbeat, 마지막 연결 만료 15초, 독립 감시 주기 0.2초입니다. 닫힌 탭의 늦은 heartbeat는 무시합니다. 브라우저가 절전·탭 정지 상태가 되어 신호를 못 보내면 만료 처리합니다. Jetson 프로세스 강제 종료·전원 단절 시의 하드웨어 정지를 보장하는 설계는 아니며 부팅 자동 시작도 미구성입니다.
