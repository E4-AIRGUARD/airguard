# 내부 연기 9조합 — 2026-10-01

오늘은 BME280을 제외한다. 현재 보드의 `ENABLE_BME280=0` 펌웨어를 사용하며 새 업로드는 필요 없다.
온도·습도·기압을 읽거나 시작 조건으로 쓰지 않는다.

## 순서와 기록

1. 연기 생성 전에 `check_smoke_start.py`로 5초 동안 센서 데이터를 읽고 최신 PM2.5가 **20 µg/m³ 이하(0~20, 경계 포함)**인지 확인한다.
   야외 측정이므로 사용자 요청에 따라 17 미만도 허용한다. 센서 최신 수신 여부도 검사하고 음수·오래된 데이터는 거부한다.
2. 사용자가 연기 생성 완료를 알리면 `measure_smoke_after_manual.py`로 해당 조합 하나만 시작한다.
   저장된 생성 전 조건 확인은 해당 조합에 대해 15분 이내여야 한다. 시간이 지났으면 연기 생성 전에 다시 확인한다.
   연기 생성 후 높아진 PM2.5에 20 이하 조건을 다시 적용하지 않는다. PMS5003 최신 데이터는 계속 필요하다.
3. 팬 가동과 함께 **300초** 동안 센서 CSV 및 **1초 간격 사진**을 저장한다. 별도 창은 열지 않는다.
4. 촬영을 멈추고 양쪽 팬 정지 명령을 보낸 뒤 실제 PWM/RPM 0을 확인한다. 완료 조합과 다음 조합을 사용자에게 알린다.
5. 사용자가 환기한다. 요청 시 다음 조합의 PM 시작 조건만 검사한다. 검사 코드는 팬 명령·보드 재시작·사진 저장을 하지 않는다.
   다음 측정도 사용자의 연기 생성 완료 신호를 기다린다.

흡기/배기 순서: **약/약 → 약/중 → 약/강 → 중/약 → 중/중 → 중/강 → 강/약 → 강/중 → 강/강**.
약=35%, 중=60%, 강=100%. 한 조합의 팬 설정은 300초 동안 유지한다.
센서 통신 중단, 오래된 PM 데이터, 팬 PWM/RPM 불일치 또는 카메라 오류 시 측정을 실패 처리하고 팬 정지를 시도한다.
정지 확인 실패는 `stop_verified=false`로 남으므로 성공으로 안내하면 안 된다.

CSV 칼럼명·순서는 다음과 같다. `Temp`, `Humidity`는 **빈칸**, `Heat_Load`는 **0**이다.

```text
Time,Temp,Humidity,Exhaust_PWM,Intake_PWM,Heat_Load,Exhaust_RPM,Intake_RPM,PM2.5
```

사진과 센서 기록의 시간은 PC 수신 시각이다. 실제 연기 생성 시각은 수동 작업이므로 정확한 시각을 안다고 기록하지 않는다.
카메라 준비 및 센서 수신 확인을 거친 뒤 팬과 기록을 시작하므로 사용자 메시지 시각과 시작 시각에는 차이가 있다.
환기 확인용 JSON은 따로 저장하며 본 실험 CSV/사진에는 포함하지 않는다.

## 실행

프로젝트 루트 `C:\airguard`에서 실행한다. `SESSION`과 `CHECK_JSON`은 실제 경로로 바꾼다.

```powershell
python -X utf8 embedded/new_fan/new_fan_t/check_smoke_start.py --session-dir SESSION --combination 1
# ready=true 확인 후 사용자가 연기 생성 완료를 알렸을 때만 다음 명령 실행
python -X utf8 embedded/new_fan/new_fan_t/measure_smoke_after_manual.py --session-dir SESSION --combination 1 --pre-smoke-check CHECK_JSON
```

조합 번호는 완료할 때마다 1씩 증가시킨다. 기존 `NN_조합명/status.json`의 `status=complete` 및 `stop_verified=true`를 확인한 뒤 다음 조합을 준비한다.
실패·중단된 조합은 자동으로 건너뛰거나 재실행하지 않는다. 같은 조합 폴더가 있으면 덮어쓰지 않고 종료한다.
세션 또는 조합 폴더에 `STOP` 파일이 있으면 시작을 막거나 진행 중 측정을 중단한다.

사용자가 팬 환기를 요청했을 때만 다음 명령을 실행한다. 양쪽 100% 환기 후 최신 PM2.5가 20 이하이면
팬 정지 및 PWM/RPM 0을 확인한다. 최대 900초 후에도 기준에 도달하지 않으면 정지하고 timeout으로 기록한다.
환기 중에는 본 측정 CSV나 사진을 저장하지 않으며, 종료 후 다음 조합의 조건을 새로 검사한다.

```powershell
python -X utf8 embedded/new_fan/new_fan_t/ventilate_smoke.py --session-dir SESSION
```

`camera_preview.py`는 별도 창의 실시간 미리보기이며 사진을 저장하지 않는다. X/Q/Esc로 닫거나
`data/camera_preview/<PID>.stop`을 만들면 종료한다. 본 측정 전에 미리보기가 종료되어 카메라를 해제했는지 확인한다.

저장 위치:

```text
data/clean_air_fan_matrix/smoke/<세션>/
  session.json                   # 실험 조건과 조합 목록
  start_checks/NN_<시각>.json     # 생성 전/환기 후 PM 확인
  NN_intake_*_exhaust_*/
    NN_intake_*_exhaust_*.csv
    images/*.jpg
    images/images.jsonl
    status.json                  # 진행/완료/실패, 정지 확인, 표본·이미지 수
```

## 2026-10-01 완료 결과

- 최종 세션: `data/clean_air_fan_matrix/smoke/20261001_141034_retry`.
- 9개 조합을 각 300초 완료했다. 각 CSV 299행, 사진 300장으로 총 2691행·2700장이다.
  CSV는 팬 가동 후 첫 DATA부터 300초 미만까지 수신된 표본을 기록한다.
- `completion_audit.json`에 스키마, 표본·사진 수, 파일과 사진 로그 대응, PWM 조합, 온습도 빈칸,
  Heat_Load 0, PM 기록, 시간 증가 및 팬 정지 확인 결과가 있다.
- 처음 97초에서 파일 교체 접근 오류가 발생한 회차는 현재 `smoke/test/`에 보존되어 있다.
  원래 세션명은 `20261001_135648`이며 원시 JSON의 절대 경로는 당시 경로다. 완료 합계에 포함하지 않는다.
- 사진 원본은 기존 `.gitignore` 정책에 따라 로컬 보관한다. Git에는 코드·CSV·JSON 및 촬영 시각 목록을 보관한다.
- 마지막 환기 후 PM2.5 20, 양쪽 PWM/RPM 0을 확인하고 PC 제어·시리얼 연결을 종료했다.
  USB 및 별도 전원은 소프트웨어로 차단하지 않았다.
