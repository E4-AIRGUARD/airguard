# AIRGUARD 팬 자동제어

온도 LSTM v6와 PM2.5 제어 요구를 흡기·배기별 최대 PWM으로 통합한다.
단일 Serial 연결, 대시보드 연결 만료 시 팬 정지, 온도·PM 독립 사건 저장과
선택적 Aiven 업로드를 지원한다. **이 폴더의 코드와 모델만으로 실행할 수 있다.**

## 폴더 구조

Python·ML 프로젝트에서 흔히 사용하는 소스, 모델, 설정, 테스트, 문서 구분을 따른다.

```text
AI/smoke/
├── src/airguard_control/       # 실제 실행 코드
│   ├── control.py             # 센서·팬 통합 실행기
│   ├── dashboard_lease.py     # 대시보드 연결과 정지
│   ├── sensor_bridge.py       # 장치 DATA 해석
│   ├── integration_store.py   # 센서·제어 기록
│   ├── paths.py               # 모델·실행 결과 경로
│   ├── temperature/           # 온도 모델 추론·판정
│   ├── pm/                    # PM 제어기
│   ├── events/                # 사건·사진 기록
│   └── storage/               # Aiven 연결·업로드
├── models/
│   ├── temperature/           # 온도 v6 .pt
│   ├── pm/                    # PM model.json
│   ├── manifest.json          # 원본 경로·크기·SHA-256
│   └── README.md              # 모델 설명과 입력 규격
├── configs/                   # 비밀정보 없는 설정 예시
├── scripts/                   # 실행·업로드·모델 검증 진입점
├── tests/                     # 장비를 쓰지 않는 회귀 검사
├── docs/                      # 운전·구조·이관 안내
├── requirements/              # 기본·업로드 의존성
├── runtime/                   # 실행 시 생성, Git 제외
├── pyproject.toml             # 패키지·실행 명령 정의
├── integrated_control.py      # 이전 명령 호환용 진입점
└── uploader.py                # 이전 명령 호환용 진입점
```

`runtime/`에는 DB·사진·로그·키가 생성된다. 소스와 함께 올리지 않는다.
온도 모델 29,193바이트와 PM 모델 10,401바이트는 작아 일반 Git 파일로 포함했다.
모델을 새로 학습하거나 성능 기준을 바꾸지 않았다. 원본 해시는 manifest로 검증한다.

## 빠른 시작

Python 3.10 이상과 장비에 맞는 PyTorch를 먼저 준비한다.
Jetson은 JetPack/CUDA에 맞는 PyTorch를 사용한다.
PyTorch를 일반 의존성에 넣어 자동 교체하지 않는다.

아래 명령은 `AI/smoke` 폴더에서 실행한다.

```bash
python3 -m pip install -r requirements/control.txt
python3 scripts/check_models.py
python3 scripts/run_control.py --help
```

기본은 팬 명령을 보내지 않는 관찰 모드다. 실제 센서 API를 지정한다.

```bash
python3 scripts/run_control.py --source-api http://127.0.0.1:8094/api/temperature
```

패키지 설치 방식도 지원한다.

```bash
python3 -m pip install -e .
airguard-control --help
airguard-upload --help
```

## 모델과 실행 결과

- 온도: `models/temperature/lstm_temperature_trajectory_v6.pt`
- PM: `models/pm/model.json`
- 모델 위치 변경: `--temperature-model`, `--smoke-model` 또는 `AIRGUARD_MODEL_DIR`
- 실행 결과 기본 위치: 이 폴더의 `runtime/`
- 결과 위치 변경: `AIRGUARD_RUNTIME_DIR` 또는 개별 DB·로그 인자

소스 설치와 editable 설치는 이 폴더의 models를 사용한다.
wheel 설치 시 모델은 설치 환경의 `share/airguard-fan-control/models`에 포함되고,
기본 runtime은 명령을 실행한 작업 폴더 아래에 생성된다.

## 검사와 상세 안내

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

- [실제 장비 운전·업로드](docs/RUNNING.md)
- [구조·이관·원본 관계](docs/ARCHITECTURE.md)
- [모델 설명](models/README.md)
- [설정 예시](configs/README.md)

기존 `python AI/smoke/integrated_control.py` 명령은 계속 동작한다.
실제 Jetson 서비스의 배포 위치는 자동으로 바뀌지 않는다.
