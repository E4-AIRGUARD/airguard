# airguard
K-뉴딜 아카데미 온디바이스AI기반 산업용 소프트웨어 개발 1기 E4 - AirGuard

## 온도 AI 실행

학습 데이터·분할·배포 모델·실행 코드는 [AI/temperature](AI/temperature/README.md)에 있습니다. 해당 안내에서 환경 설치와 가상 센서 검사를 먼저 수행하세요. 새 HTML 대시보드는 포함하지 않습니다.

```bash
python3 -m pip install 'numpy>=1.26,<2' 'pandas>=2.1,<3' 'joblib>=1.3,<2' 'pyserial>=3.5,<4' 'scikit-learn>=1.4,<2' 'matplotlib>=3.8,<4'
python3 -m unittest discover -s AI/temperature/tests -v
python3 AI/temperature/src/main_runner.py --controller-model physics --dummy --dry-run
```

PyTorch는 별도 설치가 필요하며 Jetson은 JetPack 호환 빌드를 사용합니다. 실제 USB 연결·장치 권한·펌웨어 설정은 별도로 준비합니다.

## 폴더 구성

- `embedded/new_fan/new_fan_t/`: 현재 팬 조합 실험 코드, 실험 문서와 센서 기록.
- `embedded/practice/firmware/`: 기존 부품 점검 및 통합 펌웨어 스케치.
- `embedded/practice/pc/`, `embedded/practice/jetson/`: 기존 PC 측정 도구와 Jetson 연결 도구.
- `embedded/practice/scripts/`: 기존 빌드·업로드 보조 스크립트.
- `embedded/practice/logs/`: 기존 연습 측정 기록.

2026-10-06에 기존 연습 파일을 `embedded/practice/` 아래로 모았다.
PC 도구의 상대 경로 예제는 `embedded/practice/`를 작업 디렉터리로 실행한다.
`tools/`와 대용량 `data/`, 비전 캡처 및 모델 파일은 이동 후에도 로컬 보관 대상으로 Git에서 제외한다.
포터블 Arduino 설정 파일에 절대 경로가 남아 있다면 현재 설치 위치에 맞춰 확인해야 한다.
이번 정리는 장치 업로드나 실제 측정을 수행하지 않는다.
