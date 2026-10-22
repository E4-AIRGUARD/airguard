# data

| 위치 | 내용 |
|---|---|
| `fan_power_rpm추정.csv` | 팬 조합별 RPM과 추정 전력 (5V x 0.27A x (RPM/7500)^3, 실측 아님). `_10-20만`, `_옛`은 이전 버전 |
| `arrival_times/` | 모델 학습에 쓴 "PM500에서 PM50까지 도달시간" 표 (젯슨 10/20, 10/21 측정) |
| `telemetry/` | 젯슨에서 자동제어를 돌린 에너지 로그 |
| `raw/` | 전처리 원본(`E4_AIRGUARD_*` 폴더들). 저장소에 올리지 않아요(`.gitignore`). `training/`의 통계 시간표 생성과 `benchmarks/validate.py`에 필요해요 |

- 10/18 측정(18회) 행의 CSV는 이 폴더에 없어서, 161행 모델을 그대로 다시 만들 수는 없어요. 새 데이터로 다시 학습하려면 `arrival_times/`의 CSV 형식(`Experiment, Intake_PWM[%], Exhaust_PWM[%], PM500_to_PM50_s`)을 따르세요.
