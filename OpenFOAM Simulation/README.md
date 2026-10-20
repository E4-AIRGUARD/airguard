# E4 AIRGUARD — OpenFOAM 시뮬레이션 실행 코드

챔버의 기본 유동(BASE), 추적자 제거(SMOKE), 냉각(HEAT)을 계산한 코드와 진행 개요입니다. 계산 결과·동영상·센서 데이터·웹 대시보드는 포함하지 않습니다. 케이스의 `0/`, `constant/`, `system/`은 Python 코드가 생성합니다.

## 1. 어떤 계산을 했는가

테이퍼형 챔버에 대각선 방향 입구·출구를 두고, 가정한 등가 유속을 입구 경계조건으로 적용했습니다. OpenFOAM Foundation 14의 `foamRun`, `fluid` 솔버와 SST 난류 모델을 사용하도록 코드가 작성되어 있습니다.

| 항목 | 코드 설정 |
|---|---|
| 바닥 / 상단 / 높이 | 345×250 / 410×330 / 320 mm |
| 입구·출구 면적 | 각각 40×40 mm |
| 격자 | blockMesh, 기본 설정 46,002셀 |
| 계산 구간 | 각 물리 300초, 저장 간격 1초 |
| 주변 온도 / HEAT 초기 온도 | 24.4°C / 33.91°C |
| HEAT 중력 | (0, 0, -9.81) m/s² |

진행 순서:

1. `PIPELINE_CONFIG.json`에서 형상 버전, 유속, 물성, 계산 시간과 품질 기준을 읽습니다.
2. `case_builder.py`가 격자 사전·초기장·물성·경계조건·수치 사전을 생성합니다.
3. BASE를 계산하고 후반 유동·유량 불균형 등을 점검합니다.
4. 같은 조건의 BASE 최종장을 초기 유동으로 사용해 SMOKE와 HEAT를 각각 계산합니다. HEAT가 SMOKE 결과를 이어받는 구조는 아닙니다.
5. 저장된 장을 ParaView로 읽어 CSV와 품질 기록을 생성합니다. 빠진 시각을 임의 보간하지 않습니다.
6. 선택적으로 설명용 영상을 생성합니다. 영상의 대표 유선은 고정하지만 온도·추적자 값은 저장된 시간별 장을 사용합니다.

## 2. 조건과 가정

흡기 I / 배기 E의 PWM은 35·60·100%를 사용합니다. 기준 유속 0.87·1.49·2.18 m/s는 설치 팬의 실측값이 아닌 설명용 가정입니다.

```text
Ueq = sqrt((Uref_I² + Uref_E²) / 2)
Qeq = Ueq × 0.0016 m²
```

| 신규 대표 조건 | 등가 유속 (m/s) | 같은 경계조건으로 연결한 조건 |
|---|---:|---|
| I100E60 | 1.867150 | I60E100 |
| I100E35 | 1.659714 | I35E100 |
| I60E35 | 1.220041 | I35E60 |

당시 I100E100·I60E60·I35E35는 별도 보관된 기존 계산을 사용했습니다. **이 공유본의 자동 실행 대상은 신규 대표 3조건 × 3물리입니다.** 기존 동일 단계 계산 원본은 포함하지 않습니다. 전체 9개 표기는 9개의 독립 물리 계산을 뜻하지 않습니다.

입구는 등가 속도, 출구는 압력 조건이며 벽면은 no-slip 및 단열 조건입니다. 두 팬의 서로 다른 고정 유량을 입구·출구에 강제로 주지 않습니다. 팬 날개·개별 압력곡선·누설·소비전력은 계산하지 않습니다. SMOKE의 C는 무차원 추적자이며 실제 PM2.5 농도가 아닙니다. 격자 독립성·실측 회복시간·설치 유량 검증을 마친 모델로 해석하지 마세요.

## 3. 실행 환경

Linux 또는 WSL에서 OpenFOAM Foundation 14 환경을 사용합니다. Python 3.10 이상을 권장합니다. 후처리에는 ParaView의 `pvpython`(numpy·VTK 포함)이 필요합니다. 영상 생성에는 `ffmpeg`, `ffprobe`, `xvfb-run`, `xauth`가 필요하며 병렬 계산에는 MPI가 필요합니다. `pip install`만으로 OpenFOAM과 ParaView가 설치되지는 않습니다.

아래 경로는 OpenFOAM 설치 위치가 `/opt/openfoam14`인 경우입니다. 다른 위치에 설치했다면 환경 스크립트 경로를 바꾸세요.

```bash
source /opt/openfoam14/etc/bashrc
cd E4_AIRGUARD_GitHub
python3 code/e4_airguard_pipeline.py doctor
python3 code/recovery_library.py plan
```

`doctor`는 렌더링 관련 도구도 검사합니다. 출력 오류를 확인한 후 계산을 시작하세요. Windows PowerShell에서는 OpenFOAM 계산을 실행하지 않습니다.

## 4. 계산 시작

실제 기존 자동 흐름은 production 계산에서 pilot 전제조건을 건너뛰도록 되어 있습니다. 새 환경에서는 먼저 분리된 2초 시험 케이스로 도구·격자를 확인하세요.

```bash
python3 code/e4_airguard_pipeline.py generate --physics BASE --pair I100_E60 --pilot
python3 code/e4_airguard_pipeline.py run --physics BASE --pair I100_E60 --pilot
```

대표 3조건의 BASE → SMOKE → HEAT 계산과 CSV 생성을 실행합니다. `--no-render`는 영상 생성만 뒤로 미루며 후처리용 ParaView는 여전히 필요합니다.

```bash
python3 code/recovery_library.py produce --no-render
```

병렬 계산을 사용하려면 다음처럼 시작합니다. 중단 후 재실행할 때는 프로세스 수를 유지하세요.

```bash
python3 code/recovery_library.py produce --processes 4 --no-render
```

상태 확인과 저장 후 중단:

```bash
python3 code/recovery_library.py status
python3 code/e4_airguard_pipeline.py stop
```

중단된 계산은 저장 완료 필드에서 다시 시작하도록 구현되어 있습니다. 원래 프로세스가 종료된 것을 확인한 후 같은 `produce` 명령을 사용하세요. 같은 케이스를 여러 터미널에서 동시에 실행하지 마세요.

계산 완료 후 영상이 필요할 때만 실행합니다.

```bash
python3 code/recovery_library.py render
```

기본 계산 폴더는 `results_v04/`입니다. `cases/`에 원시 계산, `csv/`에 후처리, `E4_AIRGUARD_배포/`에 공유용 CSV·영상·조건 기록이 생성됩니다. 모두 Git에서 제외합니다. 현재 코드의 HEAT 렌더링은 특정 온도 범위 경고만 있는 경우에도 허용하므로 영상 생성 성공을 수치 품질 전체 통과로 해석하지 마세요. summary JSON의 판정도 확인하세요.

## 5. 코드 구성과 공유 범위

| 파일 | 역할 |
|---|---|
| PIPELINE_CONFIG.json | 물리·유속·시간·품질 설정 |
| code/case_builder.py | 형상·격자·OpenFOAM 사전 생성 |
| code/e4_airguard_pipeline.py | 환경 점검, 생성, 계산, 중단, 후처리 |
| code/parallel_support.py | MPI 실행·재시작·필드 재구성 |
| code/recovery_library.py | 신규 대표 조건의 실행 순서 관리 |
| code/postprocess.py, recovery_post.py, foam_reader.py | 장 읽기·CSV·품질 기록 |
| code/recovery_render.py, recovery_scene.py, x_display_preflight.py | 선택적 영상 생성 |
| code/common.py | 설정·수치·파일 검증 공통 기능 |

기존 작업 폴더의 코드를 기반으로 공유본을 구성했습니다. 누락된 구형 렌더러를 참조하던 경로는 포함된 recovery 렌더러로 연결했고, 구형 snapshot 옵션은 설명과 함께 거부합니다. 물리 설정과 계산식은 변경하지 않았습니다. 기존 계산 결과와 바이트 단위 동일성을 보장하는 배포본은 아닙니다.

공유본에서는 Python 문법, 계획 출력 및 케이스 사전 생성까지 확인합니다. OpenFOAM 수치 계산·MPI·ParaView 렌더링은 이번 정리 과정에서 재실행하지 않았습니다. 실제 환경에서 위 순서로 검증해야 합니다. 공개 라이선스는 팀에서 결정한 뒤 추가하세요.
