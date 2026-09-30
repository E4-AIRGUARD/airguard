# 가열·연기 없는 팬 9조합 실험

2026-09-28 사용자 확정: 가열·연기 없음, 흡기+배기 동시 작동, 약/약부터 강/강까지
모든 조합을 **각 300초(5분)** 측정한다. 사진은 **1초 간격**으로 저장한다.
총 센서 측정 시간은 **2,700초(45분)**이며 장비 준비·조합 전환·시작 조건 대기 시간은 별도다.

| 순서 | 흡기 | 배기 | 폴더명 |
|---|---|---|---|
| 1 | 약 35% | 약 35% | intake_weak_exhaust_weak |
| 2 | 약 35% | 중 60% | intake_weak_exhaust_medium |
| 3 | 약 35% | 강 100% | intake_weak_exhaust_strong |
| 4 | 중 60% | 약 35% | intake_medium_exhaust_weak |
| 5 | 중 60% | 중 60% | intake_medium_exhaust_medium |
| 6 | 중 60% | 강 100% | intake_medium_exhaust_strong |
| 7 | 강 100% | 약 35% | intake_strong_exhaust_weak |
| 8 | 강 100% | 중 60% | intake_strong_exhaust_medium |
| 9 | 강 100% | 강 100% | intake_strong_exhaust_strong |

## 기록 조건

- 팬 단계는 35/60/100%다. 이 별도 실험에서는 흡기보다 배기가 강해야 한다는 제한을 적용하지 않는다.
- 드라이기·연기원을 사용하지 않는다. `Heat_Load`는 항상 0이며 `h`로 변경되지 않는다.
- 문 개방 외란을 추가하지 않는다. 카메라 인덱스는 1, 요청 해상도는 1280×720이다.
- 각 조합 시작 전에 같은 정상 센서 샘플에서 **PM2.5 17~20, 온도 22~26°C**를 확인한다.
  통과 전에는 센서 CSV·사진을 저장하지 않는다. 세부 규칙은 [열 실험 SSOT의 공통 시작 조건](THERMAL_EXPERIMENT_SSOT.md)을 따른다.
- 각 조합의 팬 명령 적용 후 300초를 기록한다. 초기 팬 가속 구간도 포함하며 별도 안정화 대기는 없다.
- 조합 종료 시 팬을 정지하고 다음 조합의 카메라·시리얼을 준비한다.
- `q` 또는 Ctrl+C로 중단하면 남은 조합을 실행하지 않는다. 카메라·시리얼 오류도 전체 순서를 중단한다.
- 카메라 사진은 측정 시작 후 첫 프레임부터 1초 목표 일정으로 저장한다. 전체 약 2,700장이며
  장치·저장 지연에 따라 실제 장수는 달라진다.

CSV 컬럼은 기존과 동일하다:

```
Time,Temp,Humidity,Exhaust_PWM,Intake_PWM,Heat_Load,Exhaust_RPM,Intake_RPM,PM2.5
```

## 저장 위치

`data/clean_air_fan_matrix/<실행시각>/`

- `csv/<조합>/<실행ID>.csv`: 센서 데이터
- `csv/<조합>/<실행ID>.start_check.json`: 시작 조건을 통과한 값·시각·대기 시간
- `images/<조합>/<실행ID>/*.jpg`: 해당 조합의 사진
- 사진 폴더의 `images.jsonl`: 사진별 PC 수신 시각과 경과 초
- 사진 폴더의 `run.json`: 대응하는 CSV 경로와 실행 조건
- `batch.json`: 전체 상태(`running`, `complete`, `stopped`, `failed`)와 완료 목록

센서와 사진은 같은 PC 시각 기준으로 연결한다. 하드웨어 동기 촬영은 아니다.
반복 실행은 새 폴더에 저장하므로 이전 데이터를 덮어쓰지 않는다.

## 실행

프로젝트 루트 `C:\airguard`에서 실행한다. 기본값도 300초/조합·사진 1초다.

```powershell
python embedded/new_fan/new_fan_t/measure_clean_air_matrix.py --duration-sec 300 --image-interval-sec 1
```

장비를 열지 않고 순서와 시간을 확인:

```powershell
python embedded/new_fan/new_fan_t/measure_clean_air_matrix.py --dry-run
```

기존 열 회복 실험은 `measure_thermal_response.py`의 기본 `thermal` 모드로 별도 유지한다.

## 완료 기록

- 2026-09-28 Phase0 + 팬 9조합: [측정 결과와 파일 검증 보고서](data/clean_air_fan_matrix/session_20260928_225746/MEASUREMENT_REPORT.md)
