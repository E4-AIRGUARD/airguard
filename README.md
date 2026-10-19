# airguard
K-뉴딜 아카데미 온디바이스AI기반 산업용 소프트웨어 개발 1기 E4 - AirGuard

## 폴더 구성

- [`embedded/dashboard/`](embedded/dashboard/): 에어가드 웹 대시보드 소스, 실행 안내, 팀 PR 작업 방법과 자동 검사.
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
