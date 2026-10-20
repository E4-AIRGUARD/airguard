# 에어가드 대시보드

팀원이 함께 수정하는 독립 웹 대시보드입니다. HTML, CSS, JavaScript로 구성되어 별도의 빌드나 npm 설치 없이 실행됩니다.

## 팀원이 처음 시작할 때

작업 순서는 **코드 받기 → 작업 브랜치 만들기 → 실행·수정 → 검사 → PR → 검토 후 `main` 병합**입니다.

현재는 공동 개발 단계이며 **`main`에 병합해도 공개 대시보드는 자동 배포되지 않습니다.** 팀원들이 수정하고 완성한 뒤 별도로 배포합니다.

### 1. 준비 프로그램과 접근 권한

- Git: 코드 다운로드, 브랜치, 커밋과 업로드
- Python 3: 로컬 대시보드 실행
- Node.js 22 이상: 코드 검사
- VS Code 등 코드 편집기

이 저장소는 비공개입니다. [E4-AIRGUARD/airguard](https://github.com/E4-AIRGUARD/airguard)에 접근할 수 있는 GitHub 계정이 필요합니다. 저장소가 보이지 않거나 푸시 권한이 없으면 관리자에게 계정의 접근 권한을 확인해 달라고 요청하세요.

### 2. 코드 받기와 작업 브랜치 만들기

처음 받는 경우, 코드를 저장할 위치에서 터미널을 열고 실행합니다.

```bash
git clone https://github.com/E4-AIRGUARD/airguard.git
cd airguard
git switch main
git pull --ff-only origin main
git switch -c feature/dashboard-hardware
```

이미 복제했다면 `airguard` 폴더에서 다음 명령을 실행합니다. 수정 중인 내용은 기존 작업 브랜치에 먼저 커밋하고 진행하세요.

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/dashboard-hardware
```

`feature/dashboard-hardware`는 예시입니다. 작업마다 다른 브랜치 이름을 사용하고, 아래 푸시 명령에서도 같은 이름을 사용하세요. `main`에서 직접 수정하지 않습니다.

### 3. 대시보드 실행

저장소 루트인 `airguard` 폴더에서 실행합니다.

```bash
cd embedded/dashboard
python run.py
```

Windows에서 `python` 명령이 없으면 `py -3 run.py`를 사용합니다. 브라우저에서 **http://127.0.0.1:8091**을 열고, 종료할 때는 터미널에서 `Ctrl+C`를 누릅니다. 포트가 사용 중이면 `python run.py --port 8093`으로 실행하고 **http://127.0.0.1:8093**에 접속합니다.

### 4. 수정할 파일

아래 경로는 `embedded/dashboard` 폴더 기준입니다. 파일을 저장하고 실행 중인 브라우저를 새로고침해 확인합니다.

| 파일 | 내용 |
| --- | --- |
| `dist/index.html` | 사이드바, 상단 메뉴, 모달의 기본 구조 |
| `dist/styles.css` | 색상, 간격, 카드·그래프·사진 크기, 모바일 배치 |
| `dist/app.js` | 화면 구성, 예시 데이터, 차트, 필터, 페이지 이동, 모달 |
| `dist/assets/` | 챔버 기록 사진과 제품 사진 |
| `dist/assets/hardware/sources.json` | 제품 사진과 센서 사양의 출처 |
| `run.py` | Python 표준 라이브러리로 실행하는 로컬 서버 |
| `tests/dashboard.cjs` | 페이지 이동, 필터, 전력 적분, CSV 등의 회귀 검사 |

`app.js`의 `deviceData`에서 제품 설명과 측정 사양을, `events`에서 예시 사건을 수정합니다. `EVENT_PAGE_SIZE`는 페이지당 행 수이고 `powerSamples`, `eventEnergy`, `fanReason`은 예시 전력과 팬 조합 설명을 담당합니다.

저장 후 브라우저를 새로고침하면 변경됩니다. 이전 화면이 남아 있으면 `Ctrl+F5`를 사용하세요.

### 5. 수정 결과 검사

브라우저에서 변경한 기능과 좁은 화면에서의 배치를 확인합니다. 서버를 실행한 터미널에서 `Ctrl+C`로 종료한 뒤, **`embedded/dashboard` 폴더**에서 다음 명령을 실행합니다. 추가 패키지는 필요 없습니다.

```bash
node --check dist/app.js
node tests/dashboard.cjs
```

문법 검사에서 오류가 없고 회귀 검사에서 `PASS`가 출력되는지 확인하세요. 대시보드 파일이 변경되는 PR과 `main` 푸시에서도 같은 검사가 자동 실행됩니다. 자동 검사가 화면의 모든 배치나 실제 장비 연결을 확인해 주지는 않습니다.

### 6. 커밋하고 GitHub에 올리기

앞 단계의 `embedded/dashboard` 폴더에서 저장소 루트로 이동하고 변경 내용을 확인합니다.

```bash
cd ../..
git status
git diff
```

실제로 수정한 파일만 추가합니다. 아래는 `app.js`와 `styles.css`를 수정한 경우의 예시입니다.

```bash
git add embedded/dashboard/dist/app.js embedded/dashboard/dist/styles.css
git commit -m "Improve dashboard hardware layout"
git push -u origin feature/dashboard-hardware
```

다른 파일을 수정했다면 `git add`에 해당 파일 경로를 사용하세요. 비밀번호, API 키, 개인 `.env` 파일은 올리지 않습니다. 제품 사진을 추가하면 출처도 함께 기록하고, 예시 데이터가 실제 측정값으로 오해되지 않도록 표시를 유지하세요.

### 7. PR 작성과 병합

[GitHub 저장소](https://github.com/E4-AIRGUARD/airguard)에서 **Compare & pull request**를 누릅니다. 버튼이 보이지 않으면 **Pull requests → New pull request**를 선택합니다.

- **base:** `main`
- **compare:** 본인의 작업 브랜치 (예: `feature/dashboard-hardware`)
- **제목:** 변경 내용을 간단히 작성
- **설명:** 변경 이유, 수정한 기능, 확인 결과 작성
- 화면 변경 시 스크린샷 첨부

PR 설명 예시:

```text
변경 내용
- 하드웨어 카드의 사진 크기와 설명 간격 수정

확인한 내용
- 하드웨어 화면과 상세 모달 확인
- 좁은 화면에서 배치 확인
- JavaScript 문법 검사 및 대시보드 테스트 통과
```

**`Dashboard checks`가 통과했는지 확인하고 팀원의 검토 후 병합합니다.** 충돌이 발생하면 다른 사람의 변경을 덮어쓰거나 강제 푸시하지 말고 팀원과 함께 해결하세요. 리뷰 필수 여부는 저장소 관리자가 설정한 브랜치 규칙을 따릅니다.

다음 작업을 시작할 때는 저장소 루트에서 최신 `main`을 받고 새 브랜치를 만듭니다.

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/dashboard-next-change
```

## 화면 구성

- 메인: PM2.5, 온도·습도, 컨테이너 상태, 팬 RPM·PWM, CCTV, 최근 기록
- CFD: 현재/선택 팬 조합, 조합별 영상 파일 선택과 재생
- 센서 트렌드: 기간별 변화와 기준선
- 이상·회복 기록: 날짜/상태 필터, 5개 단위 페이지 이동, 센서·전력 그래프, 팬 선택 이유 모달, 사진, CSV
- 하드웨어: 실제 제품 사진 6종, 흡기·배기 팬 통합 설명, 센서 측정 범위·오차 조건

## 공개 사이트 반영

팀 작업 명령의 간단한 요약은 [CONTRIBUTING.md](CONTRIBUTING.md)에서도 볼 수 있습니다.

현재 공개 사이트: https://airguard-monitor-20261019.woolly-wasp-7982.chatgpt.site

**현재 배포는 보류 중입니다.** GitHub Actions는 검사만 실행하며, `main`에 머지해도 공개 사이트가 자동으로 바뀌지 않습니다. 팀 작업이 완성되고 배포를 결정한 뒤 사이트 소유자가 최신 `dist/`를 검토하여 반영합니다. 사이트 계정 설정과 배포 인증 정보는 이 폴더에 포함하지 않습니다.

다른 정적 호스팅을 사용한다면 `dist/` 내용을 배포합니다.

## 데이터 구분

현재는 예시 모드이며 실제 장비와 연결되어 있지 않습니다. 센서 값, RPM, 사건, 팬 합산 전력, 선택 이유는 예시 데이터입니다. 전력 그래프의 Wh는 표본 시각에 대한 사다리꼴 적분값이며 센서·제어기 전력은 제외합니다. 실제 팬 제어 명령을 전송하지 않습니다.

CCTV와 사건 사진은 동일한 저장된 실험 사진입니다. CFD 원본 영상은 포함되어 있지 않으며 사용자가 선택한 영상은 해당 브라우저 세션에서만 유지됩니다. 실제 운영에는 인증된 장비 API, 전력 계측, 사건 저장소, 카메라와 CFD 영상 연결이 필요합니다.

제품 사진은 제조사·판매처의 자료이며 각 카드와 `sources.json`에 출처가 있습니다. 코드 공유가 사진의 권리 이전을 뜻하지 않습니다. Noto Sans KR은 Google Fonts에서 불러오며 오프라인에서는 시스템 글꼴을 사용합니다.

최초 가져오기 기준: 2026-10-19 배포본, 원본 소스 커밋 `2a4489eb5b733fac058661cbd9466fc941df72db`.
