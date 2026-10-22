# 연기 팬 자동제어 모델 (fan_control)

PM2.5(연기) 농도에 따라 흡기·배기 팬 PWM(35/60/100) 조합을 고르는 제어기와 시간 예측 모델을 모은 폴더입니다. PM이 500 아래로 내려온 뒤 마감(기본 70초) 안에 PM50에 도달하는 조합 중 추정 에너지가 가장 적은 것을 고릅니다.

## 핵심 정보

| 항목        | 내용                                                                                  |
| ----------- | ------------------------------------------------------------------------------------- |
| 모델        | 회귀 시간 예측 `1/T = k0 + k1 s(in) + k2 s(out) + k3 s(in)s(out)` (계수 5개, 약 10KB) |
| 학습 데이터 | 측정 79회 (가중치 반영 161행), 조합 9개 (PWM 35/60/100 x 흡기·배기)                   |
| 제어        | 조합 계획(동적계획법), 급상승 시 최고속, 실시간 속도 보정, 정체 감지, 최소 유지 25초  |
| 에너지      | RPM 기반 추정 `5V x 0.27A x (RPM/7500)^3 x 시간` (전력계 실측 아님)                   |
| 실행 환경   | Jetson Orin Nano, PMS5003 센서, ESP32 PWM. 제어기는 파이썬 표준 라이브러리만 사용     |
| TensorRT    | 해당 없음 (신경망이 아니라 회귀식)                                                    |

## 성능 (9회, 조건당 3회, PM500 아래 시점부터)

| 방식         | PM50까지 평균 | 추정 에너지 평균 | 마감 70초 달성 |
| ------------ | ------------- | ---------------- | -------------- |
| 자동제어     | 90초          | 103 J            | 2/3            |
| 고정 100/100 | 58초          | 188 J            | 2/3            |
| 고정 35/35   | 87초          | 23 J             | 0/3            |

정상 동작한 2회는 100/100과 같은 속도에 에너지가 55~60% 적었고, 1회는 PM50 근처에서 정체해 157초가 걸렸습니다. 자세한 표와 한계는 [`docs/최종모델_성능지표_1021.md`](docs/최종모델_성능지표_1021.md)에 있습니다.

## 알려진 한계

- 조건당 측정이 3회라 자동제어가 낫다고 단정할 수 없습니다. 에너지는 35/35가 가장 적습니다.
- PM50 근처(방 바닥값 약 45~50)에서 정체하면 도달 시간이 크게 늘어납니다. 정체 감지가 기준 경계에서 오락가락하는 문제가 남아 있습니다.
- 실제 DB 접속, 온도 제어와 합친 시험은 하지 않았습니다. 센서·팬 연결 함수(`read_pm`, `set_fan`)는 비어 있습니다.

## 폴더 구조

```
fan_control/
├─ README.md
├─ requirements.txt
├─ models/                  최종 모델 model.json, 시간표 백업, archive/ (이전 버전)
├─ src/
│  ├─ fan_controller.py       Planner, FanController, EnergyMeter
│  ├─ fan_model.py            시간표 모델 함수
│  ├─ smoke_fan.py            통합용 한 줄 인터페이스 SmokeFan.step(pm)
│  ├─ event_recorder.py       PM 이벤트(50 초과 → 회복) 기록
│  ├─ run_on_jetson.py        젯슨 실행 (read_pm, set_fan을 채워서 사용)
│  ├─ run_virtual_live.py     가상 센서 + 대시보드
│  └─ paths.py
├─ training/
│  └─ fan_model_regression.py 회귀 모델 학습
├─ benchmarks/
│  ├─ validate.py             모델 검증표 생성
│  ├─ virtual_test.py         가상 플랜트 시험
│  ├─ dummy_test.py           더미 시험
│  └─ results/                검증 결과표, 가상 시험 로그
├─ tests/                   pytest
├─ docs/                    적용 방법, 성능 지표, 벤치마크 정리, 모델 변환 절차, 변경사항
└─ data/                    전력 표, 학습에 쓴 도달시간 (전처리 원본은 올리지 않음)
```

## 빠른 시작

```
pip install -r requirements.txt        # 젯슨에서 돌리기만 하면 필요 없음

python src/run_on_jetson.py --fake     # 센서 없이 루프 확인
python src/run_on_jetson.py            # dry-run (팬은 건드리지 않음, read_pm 구현 필요)
python src/run_on_jetson.py --live --event-db pending.sqlite3   # 실제 PWM 출력 + 이벤트 기록
pytest                                 # 테스트
```

## 다시 학습·검증

```
python training/fan_model_regression.py data/arrival_times/<CSV>[:가중치] ...   # models/model.json을 덮어씀
python benchmarks/validate.py          # data/raw 필요
```

`fan_model_regression.py`는 `models/model.json`을 바로 덮어쓰므로 실행 전에 백업해야 합니다.
