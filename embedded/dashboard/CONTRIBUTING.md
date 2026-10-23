# 대시보드 공동 수정

처음 작업하는 팀원은 준비 프로그램부터 실행·수정·검사·PR까지 설명한 [README 안내](README.md)를 먼저 읽어주세요. 아래는 작업 명령의 요약입니다.

## 1. 최신 코드에서 작업 브랜치 만들기

저장소에 쓰기 권한이 있는 계정으로 복제합니다. 이미 복제했다면 `git clone` 단계는 생략합니다.

```bash
git clone https://github.com/E4-AIRGUARD/airguard.git
cd airguard
git switch main
git pull --ff-only origin main
git switch -c feature/dashboard-sensor-labels
```

브랜치 이름은 작업마다 구분해서 사용하세요. 위 예시는 센서 표시 문구를 수정하는 경우입니다.

## 2. 수정하고 확인하기

`embedded/dashboard/dist` 안의 필요한 파일만 수정합니다. 실행과 검사 방법은 [README.md](README.md)에 있습니다. 제품 사진이나 다른 정적 파일을 추가하면 관련 출처도 함께 기록하세요. 파일 경로의 대소문자는 정확히 맞춥니다.

화면에 실제 측정값과 예시값의 구분을 유지하세요. API 키나 계정 비밀번호, 개인 `.env` 파일은 커밋하지 않습니다.

## 3. 커밋과 PR

아래 명령은 저장소 루트 기준입니다. 변경한 파일을 먼저 확인하고 필요한 파일만 추가합니다.

```bash
git diff -- embedded/dashboard
git add embedded/dashboard/dist/app.js embedded/dashboard/dist/styles.css
git commit -m "Update dashboard sensor labels"
git push -u origin feature/dashboard-sensor-labels
```

GitHub의 **Compare & pull request**를 눌러 대상 브랜치를 `main`으로 선택합니다. PR에는 변경 이유, 확인한 화면과 검사 결과를 적고 화면 변경은 스크린샷을 첨부합니다. `Dashboard checks`가 통과했는지 확인하고 팀원의 검토 후 병합하세요. 자동 검사는 로그인이나 실제 장비 연결을 검증하지 않습니다.

`main`에 병합된 코드는 다음 작업 전에 다시 받아야 합니다. 다른 사람의 변경을 덮어쓰는 강제 푸시는 사용하지 않습니다. 리뷰 필수 여부는 저장소 관리자가 설정한 브랜치 규칙을 따릅니다.

## 4. 기존 공개 사이트에 적용

현재 배포는 보류 중입니다. GitHub Actions는 검사만 실행하며, `main`에 병합해도 공개 대시보드에 자동 반영되지 않습니다. 팀 작업이 완성된 뒤 배포를 결정하고 사이트 소유자가 반영합니다.
