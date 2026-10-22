# 설정 파일

`aiven.env.example`은 접속 형식을 보여 주는 빈 예시다.
실제 값을 넣은 파일은 `runtime/aiven/.env`에 보관하고
`--env-file`로 업로더에 전달한다. CA 인증서도 runtime에 둔다.

통합 제어 설정은 `scripts/run_control.py --help`의 실행 인자로 지정한다.
`AIRGUARD_MODEL_DIR` 환경변수는 models 폴더 위치를,
`AIRGUARD_RUNTIME_DIR`는 로그·DB·사진 기본 저장 폴더를 바꾼다.
이 두 환경변수는 실행 환경에 직접 설정하며 Aiven .env에서 읽지 않는다.

카메라 주소, Serial 포트, 대시보드 키 경로는 각 장비에 맞게 지정한다.
새 폴더 구조로 정리하면서 제어·회복 판정 기준은 변경하지 않았다.
