# PM 이벤트 전달본 적용 기록 — 2026-10-21

## 입력과 비교

먼저 `C:/vision/deliverables/AIRGUARD_DASHBOARD_INTEGRATION_WORK_LOG_20261021.md`를 읽고, 현재 README·DASHBOARD_SSOT와 대조했습니다. 사용자 제공 `변경사항.md`, `fan_control_final_통합용 (1).zip`은 전달본 자료로 사용했습니다. 문서의 예제 실행·DB ALTER 명령은 자동 실행하지 않았습니다.

- ZIP SHA-256: `bb8c91bbd304cd4cfedae315bbe3c0893698260b94f3e3ee32db2c53b9f5e69e`.
- 원본 추출: `C:/vision/extracted/fan_control_final_integration_20261021/fan_control_final`.
- 변경 전 백업: `C:/vision/backups/fan_event_integration_before_20261021_214049`.
- 적용 저장소: `C:/vision/airguard-dashboard-collab/embedded/dashboard`.
- 줄바꿈을 정규화해 비교한 결과 팬 제어기·회귀 모델·가상 시험 코드의 내용은 동일했습니다. 기존 모델과 제어기를 교체하지 않았습니다.
- 기존 실제 실행기 `smoke_fan_final/integration_example.py`와 `.original.py` 구분 및 모델 JSON 파일 핸들 정리 개선을 유지했습니다.

## 반영 내용

1. `event_recorder.py`, `uploader.py`, `schema.sql`, 전달본 변경사항을 추가하고, `smoke_fan.py`, `run_on_jetson.py`, 통합 가이드를 갱신했습니다.
2. `pm_recovery.py`를 실제 통합 루프에 연결했습니다. 기본 `runtime/pm_pending.sqlite3`, `--event-db`, `--device-id`를 지원합니다. MySQL 네트워크 작업은 제어 루프에서 수행하지 않습니다.
3. PM 최대 3표본 중앙값 >50에서 이벤트를 시작하고 <=50을 3초 유지하면 completed입니다. 전달본의 중앙값 판정은 유지합니다. 통합 경로에서 2.5초 초과 표본 공백, 미수신·센서 오류는 유지시간과 중앙값 버퍼를 초기화하며 중복 시각은 제외합니다. NaN/Inf·음수 PM을 유효 표본으로 처리하지 않습니다.
4. UTC 센서 시각, 실제 DATA PWM/RPM, 유효한 온도·습도를 기록합니다. 통합 팬 요구·실제 피드백·단조 증가 제어 시계를 구분합니다. 탭 연결이 없어 팬이 0/0이어도 센서 PM 사건은 계속 기록됩니다.
5. category=pm25를 JSON에도 저장하고, 정상 종료·재시작에서 진행 중 사건을 interrupted 처리합니다. 비정상 종료 후에는 마지막 저장 표본 시각을 중단 시각으로 사용합니다. 기존 온도 사건·영상 연기 사건은 별도로 유지합니다.
6. 업로더의 Windows/Linux 단일 실행 잠금, 실패 시 큐 유지·rollback, 연결 종료, `--once` 실패 종료 코드를 보완했습니다. 원격 commit 이후에만 큐 항목을 삭제합니다. CA 지정 시 인증서와 호스트 이름을 검증합니다. 선택 category ALTER는 주석으로 바꾸고 별도 의존성 `requirements-upload.txt`를 추가했습니다.
7. SQLite 큐·부속 파일을 Git 제외 목록에 추가하고, README·SSOT·검사 실행기에 반영했습니다. 원본 가상 에너지 로그를 실측 결과로 가져오지 않았습니다.

## 검증 결과

- `python tests/run_all.py`: **38건 통과** (기존 26건 + PM 이벤트/업로더 12건).
- 신규 검사: 중앙값·3초 회복, 경계값 50, 중복·결측·센서 오류·표본 공백, 비유한 값, 실제 PWM/RPM·온습도·UTC 시각, 중단/재시작, category 보존, 연결 정지 시 실제 0/0 기록, 단조 시계 분리, 업로드 성공/실패/잠금.
- 기존 실제 온도 v6 CPU 추론 검사 포함.
- `node tests/dashboard.cjs`: 통과.
- `dist/*.js` 전체 `node --check`: 통과.
- 최초 샌드박스 실행의 localhost 프록시 2건은 WinError 10013으로 차단되었습니다. localhost 검사가 허용된 실행에서 전체 38건을 다시 실행해 통과했습니다. 외부 장비/DB 호출은 사용하지 않았습니다.

## 적용 범위

이번 변경은 로컬 소스입니다. Git 커밋·push·PR, 실행 중 Jetson의 파일 교체·재시작, MySQL 스키마 변경·실제 업로드, 공개 배포는 수행하지 않았습니다. 따라서 이전 작업 기록의 실제 Jetson 서비스에는 아직 새 PM 큐 기능이 적용되지 않았습니다.

PM 사건 목록 UI는 이번에 추가하지 않았습니다. 온도 사건은 기존 SQLite 조회를 유지합니다. 새로운 온도 회복 정책이나 두 카테고리 병합은 도입하지 않았습니다. 실장비 PM 회복 성능과 실제 MySQL 접속은 미검증입니다.
