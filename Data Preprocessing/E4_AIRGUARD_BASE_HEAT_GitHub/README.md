# E4 AIRGUARD — BASE·HEAT 센서 전처리

무풍 기준, 일상 팬 조합 운전 및 가열 실험의 원본 센서 CSV를 정리하는 오프라인 전처리 도구입니다. SMOKE 전처리와 별도 저장소로 사용할 수 있습니다. 실시간 팬 제어·대시보드·Aiven DB 수신 기능은 포함하지 않습니다.

## 실행 환경

- Python 3.10 이상. pip로 설치할 외부 패키지는 없습니다.
- Windows / Linux / Jetson에서 경로를 지정해 실행할 수 있습니다.
- 입력 경로를 생략하면 tkinter 폴더 선택 창을 엽니다. GUI가 없는 환경은 `--input`을 사용하세요.
- 발표용 PNG는 Windows 차트 기능을 사용합니다. 사용할 수 없으면 HTML 그래프로 저장합니다.

## 빠른 시작

실제 데이터는 로컬 `data/`에 넣고 다음처럼 실행합니다. Linux에서는 환경에 따라 `python` 대신 `python3`을 사용하세요.

```bash
python 전처리_BASE_HEAT.py --input data --output results/experiment
python 전처리_BASE_HEAT.py --input "data/온도실험" --kind HEAT_30S --output results/heat
python 전처리_BASE_HEAT.py --help
```

입력 폴더 자체 또는 바로 아래 폴더의 이름으로 실험 종류를 인식합니다. 날짜와 설명이 붙은 폴더명도 지원합니다. 명시적으로 `--kind`를 지정하면 선택한 폴더 전체에 그 종류가 적용되므로 서로 다른 실험을 섞지 마세요.

| 폴더명 | 종류 | 내용 |
|---|---|---|
| baseline | BASE_OFF | 무풍 정상 기준 |
| fan_matrix | FAN_MATRIX | 팬 조합 정상 운전 |
| heating_5s | HEAT_5S | 5초 가열 후 측정 |
| heating_30s / heat_30s | HEAT_30S | 30초 가열 및 추가 냉각 |

## 입력 CSV

UTF-8(BOM 포함 가능) 또는 CP949를 읽습니다. 시간은 ISO 형식의 `Time`, `pc_timestamp`, `Timestamp` 중 하나, 또는 `elapsed_ms`가 필요합니다.

| 측정값 | 허용 원본 칼럼명 |
|---|---|
| 온도 | Temp / temp_c |
| 습도 | Humidity / rh_pct |
| PM2.5 | PM2.5 / pm2_5 |
| 흡기 PWM | Intake_PWM / duty_in_pct |
| 배기 PWM | Exhaust_PWM / duty_out_pct |
| 흡기 RPM | Intake_RPM / rpm_in |
| 배기 RPM | Exhaust_RPM / rpm_out |
| 가열 상태 | Heat_Load (HEAT_30S에 필수) |

선택 칼럼 `bme_err`, `pms_stale`가 있으면 센서 오류·오래된 표본 판정에 반영합니다. 이미 전처리한 CSV는 원본으로 다시 입력할 수 없습니다.

30초 가열 실험의 추가 냉각은 `cooling/` 아래에서 본 측정과 대응하는 경로를 찾고 시각·메타데이터 등을 검사한 후 연결합니다. 임의의 CSV를 단순히 이어 붙이지 않습니다. 연결 불가 기록은 검토용으로 분리합니다. 구체적인 조건은 코드의 `cooling_match`, `input_groups`와 생성된 처리 안내에 있습니다.

## 처리와 결과

기존 첨부 코드의 처리 로직을 그대로 유지했습니다. 시각·중복·결측·범위와 센서 오류를 점검하고, 온도·습도·PM2.5 및 일정 PWM 구간의 RPM에 후행 3표본 중앙값을 적용합니다. 결측을 임의로 보간하지 않습니다.

- `01_AI전달_CSV/`: 처리한 센서 데이터
- `02_데이터분석_요약/`: 실험별 통계와 HTML 그래프
- `03_처리기록/`: 원본 연결, 변경·제외 기록, 적용 설정
- `README_전처리과정.md`: 실행 결과와 상세 처리 설명

`--output` 생략 시 코드 옆 `E4-AIRGUARD 데이터 품질·통합 평가`에 저장합니다. 기존 결과가 있으면 번호를 붙인 새 폴더를 만듭니다. 원본은 수정하지 않습니다. `--replace`는 호환 옵션으로 남아 있으나 실행하면 거부됩니다.

**온도 회복 완료 시각·회복 시간 정답은 생성하지 않습니다.** 냉각 통계와 회복 완료 판정은 다릅니다. 회복 판정을 추가하려면 기준 온도·허용 범위·유지 시간 등의 정의가 필요합니다. 서로 다른 날짜·초기 환경의 차이를 팬 효과로 단정하지 마세요. 학습·검증 데이터는 원본 실험(Recording_ID) 단위로 분리하세요.

## GitHub 공유

이 폴더의 코드, README, `.gitignore`를 새 저장소에 올리면 됩니다. 실제 데이터와 생성 결과는 제외하도록 설정했습니다. 처리 기록에는 실행 환경의 입력 경로가 들어갈 수 있으므로 결과물을 별도 공유할 때 확인하세요.

이 패키지는 업로드 준비용이며 GitHub에 자동 게시하지 않습니다. 공개 라이선스는 임의로 지정하지 않았습니다. 팀의 배포·재사용 정책을 정한 후 LICENSE를 추가하세요.
