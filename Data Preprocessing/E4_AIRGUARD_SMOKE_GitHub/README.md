# 전처리_SMOKE

CSV와 두 JSONL 측정 방식을 하나의 실행 파일로 처리합니다. PM2.5 전처리, 팬 시작 기준 정렬, PM500→50 감소 비교, 그래프와 점검 기록을 생성합니다.

**Python 3.10 이상. 외부 패키지 설치 없이 실행합니다.** GUI 폴더 선택은 tkinter가 있는 환경에서 지원하며, 없으면 `--input`을 사용합니다. OpenFOAM 실행 코드는 포함하지 않습니다.

## 실행

저장소를 내려받고 `전처리_SMOKE.py`가 있는 폴더에서 실행하세요.

```bash
python 전처리_SMOKE.py
```

폴더 선택 창에서 RAW 폴더를 선택합니다. 결과는 기본적으로 `전처리_SMOKE.py` 옆 `E4_AIRGUARD_SMOKE_분석결과`에 생성됩니다. 기존 결과가 있으면 `_02`, `_03`을 붙이며 덮어쓰거나 삭제하지 않습니다.

명령행으로 입력·출력을 지정할 수도 있습니다.

```bash
python 전처리_SMOKE.py --input "RAW 폴더 경로" --check-input
python 전처리_SMOKE.py --input "RAW 폴더 경로" --output "results/측정결과"
python 전처리_SMOKE.py --input "RAW 폴더 경로" --reference "이전 분석 결과 폴더"
```

처리 후 `00_분석결과_여기부터.html`을 브라우저로 여세요. 그래프는 SVG/HTML이고 인터넷 연결이 필요 없습니다.

## 지원 입력

| 형식 | 필요한 파일 | 팬 시작 기준 |
|---|---|---|
| CSV | 센서 CSV, 같은 폴더의 `status.json`(팬 정렬 시 필요) | `ventilation_started`와 PWM OFF→ON 교차확인 |
| 기존 JSONL / intermittent | `trial.json`, `status.json`, `protocol.json`, `records.jsonl` | `eval_start`와 같은 시각/PWM의 `command` 확인 |
| 신규 JSONL / space | 위 4개 파일, `space_runs` 중첩 구조 또는 `operator_input_channel=local_space` | 위와 동일. 측정 절차·종료 조건은 메타데이터로 별도 보존 |

CSV 필수값은 PM, 흡기 PWM, 배기 PWM, 측정 시각입니다. 온습도/RPM이 없어도 SMOKE 분석이 가능합니다. CSV의 지원 별칭은 `airguard/readers.py`의 `ALIASES`에 있습니다.

```text
CSV_RAW/                    JSONL_RAW/
└─ 회차폴더/                └─ raw_runs/
   ├─ sensors.csv              └─ [space_runs 또는 intermittent_runs/]
   └─ status.json                 └─ 회차폴더/
                                     ├─ trial.json
                                     ├─ status.json
                                     ├─ protocol.json
                                     └─ records.jsonl
```

JSONL의 평면/중첩 회차 폴더를 모두 탐색합니다. `excluded_runs`, `fail_test`, 미완료 회차는 사유를 기록하고 제외합니다. 알 수 없는 측정 방식은 정상 실험으로 추측하지 않습니다.

```bash
python 전처리_SMOKE.py --input "RAW 폴더" --format jsonl --series space
python 전처리_SMOKE.py --input "RAW 폴더" --format jsonl --series intermittent
```

`auto`는 JSONL의 space 자료가 있으면 space를 선택합니다. 이전 방식도 함께 들어 있으면 선택하지 않은 회차를 기록합니다. CSV와 JSONL이 섞인 입력은 `--format`을 지정해야 합니다. 출력 CSV는 다시 RAW로 사용하지 마세요.

## 전처리와 평가 기준

1. 결측·숫자 변환·유한값·음수 PM·PWM 범위·시각·중복·공백을 확인합니다.
2. PM에 후행 3표본 중앙값을 적용합니다. 첫 2개 표본은 유지하고 PWM/phase/오류/시간 공백 경계에서 초기화합니다.
3. 필터 결과의 500 초과 값을 500으로 제한합니다. 상한은 프로젝트 분석 기준이며 센서의 절대 유효 한계나 실제 농도 500을 뜻하지 않습니다.
4. 같은 전처리 결과에서 전체 구간, 팬 시작 구간, PM500→50 구간을 추출합니다. 구간마다 필터를 다시 적용하지 않습니다.

기본 무효행 정책 `legacy`는 기존 분석 재현용입니다. CSV는 필수값 오류행을 제외하고 JSONL은 무효 PM 표본을 공백과 점검 기록으로 남깁니다. 정책을 통일하려면 별도 `config.json`에서 `keep` 또는 `drop`을 선택하고 새 결과를 비교하세요. 실제 0은 보존하며 보간하지 않습니다.

주요 지표:

- **팬 시작→PM50 유지 확인:** 팬 명령 시각부터 전처리 PM50 이하가 5초 유지되었다고 확인한 시각까지. 구간 시작과 확인 시각을 각각 저장합니다.
- **PM500→50:** 마지막 상한 표본 이후 PM500 미만 5초 유지 구간의 시작부터 PM50 이하 5초 유지 구간의 시작까지. 첫 농도는 실제 표본값이며 가짜 PM500 표본을 만들지 않습니다.
- 유지 미확인은 공백과 사유로 표시합니다. 0초 또는 실패로 대체하지 않습니다.

기본 설정은 기존 세 코드의 기준을 유지합니다. 평활화가 AI 예측 성능을 개선했다는 의미는 아닙니다. `median_window=1`은 무필터 비교용입니다. PWM은 전력/유량 실측값이 아닙니다.

## 생성물

```text
E4_AIRGUARD_SMOKE_분석결과/
├─ 00_분석결과_여기부터.html
├─ 전처리_및_분석_설명.md
├─ 01_전체구간_데이터전처리/
├─ 02_팬시작0초_회복분석/
├─ 03_PM500_PM50_감소비교/
├─ 04_데이터분석_요약/
└─ 05_전처리검증_및_처리이력/
```

`00_PM25_팬조건_전체비교.svg`, `01_PM25_9조건비교.svg`, 흡기 고정 비교, 개별 그래프를 생성합니다. 정확히 같은 I/E 조합만 같은 색이며 반복 회차는 선 스타일을 바꿉니다.
9조건 패널은 시간축과 농도축을 공통 범위로 표시하므로 조건별 감소 속도를 비교할 수 있습니다.

시계열 공통 칼럼은 `Measurement_Timestamp`, `Time[s]`, `PM2.5[ug/m3]`, `PM2.5_Raw[ug/m3]`, `PM_Capped[0/1]`, `Intake_PWM[%]`, `Exhaust_PWM[%]`입니다. 02는 `Original_Time[s]`가 추가됩니다. ID/측정 방식/원본 경로/분할 정보는 별도 요약과 메타데이터에서 확인합니다.

## 실험별 예외와 AI 학습

- 과거 A10/A11 제외는 **20261015·20261016의 intermittent** 회차에만 적용합니다. 20261018 space의 A10/A11은 다른 실험입니다. 규칙은 `config.json`에서 관리합니다.
- `review_runs`로 날짜·방식·ID와 사유를 지정하면 참고용으로 분리할 수 있습니다. 과거 A07 등 추가 의심 회차는 원 측정 검토 결과에 따라 여기에 명시하세요. 이번 통합은 임의의 신규 제외 규칙을 추가하지 않습니다.
- 측정 절차 변경/팬 명령 불일치는 참고용으로 보존하고 기본 비교 그래프에서 분리합니다.
- 전체/팬 이후/감소 구간은 같은 실험입니다. 중복 학습하거나 동일 회차를 학습/시험 양쪽에 넣지 마세요.
- `development/holdout` 원본 지정은 보존합니다. 회복시간은 미래 구간을 보고 산출한 평가값이므로 실시간 예측 입력으로 사용하지 마세요.
- 측정 날짜/연기 투입량/대기시간/주변 환경 차이는 시간 정렬만으로 제거되지 않습니다.

## 코드 구성과 확장

```text
전처리_SMOKE.py                 실행 진입점
airguard/readers.py      형식별 파일 탐색·시각/명령 해석
airguard/preprocess.py   공통 시그널 처리
airguard/analysis.py     공통 회복시간 정의
airguard/reports.py      공통 CSV·그래프·설명 생성
airguard/reference.py    이전 전처리 결과와의 선택적 참고 비교
airguard/common.py       공통 수치/파일 유틸리티
airguard/cli.py          옵션과 실행 순서
config.json             처리 설정·측정별 예외
```

새로운 입력 형식은 `readers.py`에서 공통 표본 구조로 변환합니다. 시그널 처리와 회복 정의는 복제하지 않습니다. 임베디드 측에서는 `schema_version`과 `protocol_version`을 명시하는 방식으로 입력 규격을 합의하는 것을 권장합니다. 현재 코드가 임의의 미래 형식을 자동 지원하는 것은 아닙니다.


GitHub에는 이 폴더의 코드, config.json, README.md, .gitignore를 올리세요.
