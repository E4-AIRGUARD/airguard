# AirGuard 연기 탐지 (AI/Integration)

## 프로젝트 개요
- 목적: 웹캠 사진으로 연기 있음/없음을 분류하는 AI 모델 (위치·박스 탐지는 지금 범위 아님)
- 전체 흐름: PMS5003 센서 1차 감지 → 웹캠+AI 2차 확인 → 흡기/배기 팬 제어(히스테리시스)
- 담당 범위: 이 폴더(`AI/Integration/`)만. 펌웨어·센서 실험은 `embedded/`(다른 팀원 담당)
- 배포 대상: Jetson → ESP32(`embedded/firmware/T08_cleanbox_fw`)에 시리얼 명령 (연동 방식 확인 필요)

## 기술 스택
- Python 3.10 (Jetson 실습 환경 기준. PC도 3.10으로 맞춤)
- Ultralytics YOLO11n-cls, OpenCV, pyserial, pandas
- 학습: 로컬 Jupyter CPU, 부족하면 Colab
- 배포(나중): Jetson Orin Nano 8GB, TensorRT `.engine`, 전력 모드 MAXN(`sudo nvpmodel -m 0`)
- Jetson 웹캠: `/dev/video0`, MJPG 포맷

## 코드 규칙
- 주석·docstring·출력 메시지는 한국어
- 파일 맨 위 docstring에 용도와 사용 예 기록
- 경로·임계값·시리얼 포트는 하드코딩 금지, 인자나 설정 파일로
- Windows 콘솔(cp949) 출력 깨짐 방지 코드 유지

## 작업 프로세스
1. 이슈 초안 작성 → 확인 후 생성
2. 브랜치 생성
3. 기존 코드·테스트 확인 후 구현
4. 테스트 실행. 학습/평가를 바꿨으면 평가 기준대로 결과 기록
5. README나 이 파일에 바뀐 명령·구조 반영
6. 브랜치에 커밋 후 PR
7. merge된 브랜치는 삭제

## 데이터 규칙
- 현재 구조: 조건(팬 조합)별 하위 폴더 없이 한 폴더에 모아 둠
  ```
  image/datasets/
    train/with_smoke/      # 01,03,04,05,06,07,09 조건 사진
    train/without_smoke/
    val/with_smoke/        # 02,08 조건 사진
    val/without_smoke/
  ```
- train/val은 조건 단위로 나눈다 (02·08 = val, 나머지 = train). 같은 조건의 사진을 train과 val에 섞지 않는다.
- `without_smoke` 폴더 안의 이미지는 연기 없을 때 사진
- 웹캠 자동 노출·화이트밸런스는 끄고 수집·추론

## 평가 기준
- 정확도보다 재현율(놓침)과 오탐률 우선
- 최종 판정: 여러 프레임 투표 (예: 최근 15프레임 중 10 이상이 연기면 연기. 수치 확인 필요)
- 기준선: 빈 박스 사진과의 차이로 판단하는 배경 차분. 같은 val로 AI 모델과 비교
- 목표: 재현율 95% 이상, 오탐률 1% 이하

## Git 규칙
- 브랜치: 새 기능을 개발하는 브랜치일 경우 `feat/기능명`, 버그 수정을 위한 브랜치일 경우 `fix/기능명`.
- 커밋 메시지: `feat:` / `fix:` / `docs:` / `refactor:` (예: `feat: 프레임 투표 추론 스크립트 추가`)
- IMPORTANT: 커밋 메시지, 이슈·PR 제목과 본문은 접두어를 빼고 모두 한글로 쓴다. main 직접 push 금지.
- PR 하나에 기능 하나
- 커밋 작성자는 사용자 본인 GitHub 계정(KangJiUng)으로 한다. `Co-Authored-By: Claude` 같은 공동 작성자 줄과 Claude 환경의 서명은 넣지 않는다.
- 이슈: 작업 요청을 받으면 이슈 초안을 먼저 보여주고, 확인받은 뒤 `gh issue create`로 만든다.
  - 제목(subject) 규칙
    1. 50글자를 넘지 않는다
    2. 마침표·특수기호를 쓰지 않는다 (접두어 뒤 `:`만 예외)
    3. 접두어는 소문자, 명령문으로 쓴다. 접두어 뒤 한글은 `~추가`, `~수정`처럼 명사형으로 끝낸다
    4. 개조식으로 간결하게, 요점만 쓴다
    - 예: `feat: 프레임 투표 추론 스크립트 추가`
  - 본문(body) 규칙
    1. 한 줄은 72자 이내로 쓴다
    2. 최대한 상세히 쓴다
    3. 어떻게보다 무엇을, 왜 바꾸는지 쓴다
  ```
  ## 목적
  ## 작업 내용
  - [ ] (여러 개면 체크리스트)
  ## 참고사항
  ```
- PR: 관련 이슈를 `Closes #번호`로 연결한다.
  - 제목: `타입: 내용` 형식, 접두어는 소문자 (예: `fix: 드롭아웃 레이어 위치 수정 및 오버피팅 완화`)
  - 타입 (하나 이상 선택)
    - `feat`: 새로운 기능 추가
    - `bug`: 버그 발견
    - `fix`: 버그 수정
    - `docs`: 문서 수정
    - `style`: 코드 포맷팅, 세미콜론 누락 등 코드 변경이 없는 경우
    - `refactor`: 코드 리팩토링
  ```
  ## 변경사항
  ## 검증
  (학습/평가 변경 시: 사용한 split, 재현율·오탐률, 기준선 대비 결과)
  ## 관련 이슈
  Closes #번호
  ```
- `.gitignore`에 추가: `datasets/**/*.jpg`, `datasets/**/*.png`, `runs/`, `*.pt`, `*.onnx`, `*.engine`, `.ipynb_checkpoints/`, `.env`

## 하지 말아야 할 것
- IMPORTANT: 원본 사진 수정·삭제 금지. 정리 결과는 별도 폴더에 만든다.
- IMPORTANT: 이미지·모델·`.env`·API 키·토큰·인증 파일 커밋 금지.
  - 2026-10-22 사용자 명시 요청에 따른 배포 모델 예외:
    `models/temperature/lstm_temperature_trajectory_v6.pt`와
    `models/pm/model.json`은 원본 SHA-256을 manifest에 기록해 포함한다.
    그 외 모델·사진·인증 파일·실측 데이터는 계속 제외한다.
- IMPORTANT: `AI/Integration/` 밖 파일 수정·커밋 금지. `git add .` 대신 `git add AI/Integration/`.
- IMPORTANT: 학습/평가 결과 없이 "성능 개선됐다"고 하지 않는다.
- 의존성 버전 임의 변경 금지
- IMPORTANT: `git push --force`, `git reset --hard`처럼 무언가를 강제로 덮어쓰는 명령은 실행 전에 한 번 더 물어보고, 확실한 승인을 받은 뒤에만 실행한다.

## 완료 기준
- `git status`에 `AI/Integration/` 밖 변경 없음, 커밋 금지 파일 스테이징 없음
- 스크립트가 오류 없이 끝까지 실행되고 테스트 통과
- PR이 위 형식대로 작성됨
