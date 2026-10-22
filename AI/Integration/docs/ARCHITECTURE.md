# 구조와 이관 기준

현재 저장소 경로는 `AI/Integration`이다. 사용자 요청에 따라 기존 smoke 폴더 전체를
이동했으며 `fan_control`과 `vision`의 내부 구조·원본 데이터도 함께 보존했다.
통합 실행 명령은 저장소 루트에서 `python AI/Integration/scripts/run_control.py`다.

2026-10-22 사용자의 폴더·모델 정리 요청에 따라 `AI/Integration`를 독립 실행 가능한
Python 프로젝트로 정리했다. 실행 코드와 모델에 대한 `embedded/dashboard`
런타임 경로 의존성을 제거했다. 원래 대시보드 파일과 Jetson 실행본은 보존했다.

## 실행 흐름

`scripts/run_control.py` 또는 `airguard-control`이 `airguard_control.control`을 실행한다.
단일 Serial 소유자가 읽은 표본을 온도·PM 제어기에 전달하고 각 팬별 최대 PWM을 적용한다.
성공한 명령만 온도 제어기에 확인하며, 대시보드 연결 만료에 따른 0/0이 우선한다.
실제 피드백의 RPM으로 에너지를 한 번 적분한다.

`events`는 동일 표본으로 온도·PM 사건을 별도 기록하고 기존 카메라 API의 최신 프레임을
재사용한다. `storage` 업로더는 별도 프로세스로 실행한다.

## 원본과 이동 위치

| 기존 위치 | 새 위치 |
| --- | --- |
| `AI/Integration/integrated_control.py` 구현 | `src/airguard_control/control.py` |
| PM 제어기·SmokeFan | `src/airguard_control/pm/` |
| 온도 v6 필수 추론·조회 모듈 | `src/airguard_control/temperature/` |
| PMRecovery·사건·사진 기록 | `src/airguard_control/events/` |
| DB 연결·업로더 | `src/airguard_control/storage/` |
| 온도 모델·PM 모델 | `models/temperature/`, `models/pm/` |
| 의존성 목록 | `requirements/` 및 `pyproject.toml` |

기존 실행 명령 호환을 위해 루트의 integrated_control.py와 uploader.py는 짧은 진입점으로
남겼다. 실제 구현은 src에만 있다. 원본의 sys.path 변경은 패키지 내부에서 제거하고
패키지 이름을 사용하는 import로 바꿨다. 설치 없이 실행하는 scripts만 src 경로를 연결한다.

온도 모델과 PM 모델은 원본 바이트를 그대로 복사했다. 출처·SHA-256·크기는
models/manifest.json에 기록했다. PM 모델의 원본 데이터가 필요한 재학습 CLI는
배포 패키지에 포함하지 않았다. 이번 작업은 재학습이나 제어 정책 변경이 아니다.

## Git에 포함하는 항목

소스, 테스트, 실행 안내, 설정 예시, 그리고 사용자가 정리를 요청한 온도·PM 모델 두 개를
포함한다. 두 모델은 각각 약 29KB와 10KB이므로 일반 Git 파일로 관리한다.
새 체크포인트와 영상 모델을 무조건 추적하도록 ignore 규칙을 풀지는 않았다.

카메라 v7 영상 모델은 별도 영상 서비스의 자산이다. 팬 제어 코드가 이를 직접 로드하지
않으므로 여기에 중복 저장하지 않는다. 키·실제 .env·인증서·사진·실측 DB·로그는 runtime에
두고 Git에서 제외한다. 큰 학습 결과·데이터셋을 나중에 공유할 때는 별도 저장소 정책을 정한다.
