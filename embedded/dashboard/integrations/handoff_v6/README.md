# AirGuard 온도 제어 전달본

이 폴더만 전달하면 됩니다. 기존 개발본의 학습·비교·대시보드·DB·다른 모델은 포함하지 않았습니다. 선택 구성은 **LSTM v6 + 원본 상승률 보조 + RPM 기반 추정 전력**입니다. 26°C 상한 유지와 실측 에너지 절감이 검증된 안전 시스템은 아닙니다. 특히 고출력보다 저출력을 더 차갑게 예측하는 후보 반응과 Jetson 통합 지연은 추가 검증 대상입니다.

## 파일

```text
handoff_v6/
  README.md
  models/lstm_temperature_trajectory_v6.pt
  src/
    main_runner.py             실행·최종 팬 전송·종료 처리
    temperature_controller.py 온도 판단 API와 고정 운전 설정
    lstm_controller.py         v6 로딩·추론·모델 해시 검사
    trajectory_lstm_v6.py      모델 구조
    trajectory_control.py     상한·상승률·에너지 후보 비교
    control_types.py           판단 결과 자료형
    sensor_receiver.py         ESP32 USB Serial 수신·공유 송신
    sensor_buffer.py           최근 8개 샘플·중앙값 평활화
    temperature_features.py    실제 시간 간격 기반 변화율
    raw_rise_assist.py         원본 상승률 보조
    fan_control.py             팬 명령 인코딩
    fan_power.py               공통 RPM 기반 추정 W/Wh
    event_store.py             센서·제어·모델 정보 DB 기록
```

실행 시 `runtime/edge_events.db`가 자동 생성됩니다. 모델 선택·정책 버전·horizon·window 옵션, Physics/threshold/구버전 LSTM, 더미 센서, 시험 ID·키보드 가열 기록은 제거했습니다. 시연에서 가열 이벤트 기록이 필요하면 팀원의 통합 이벤트 기록기로 처리합니다. 학습·평가 원본은 상위 개발 프로젝트에 보존되어 있습니다.

## 실행

Python 3.10 이상과 기존 Jetson 호환 PyTorch, NumPy, pyserial이 필요합니다. 학습이나 scikit-learn/joblib/Streamlit은 필요하지 않습니다. 기존 요청에 따라 requirements 파일은 전달본에 추가하지 않았습니다. Jetson 호환 패키지를 임의 업그레이드하지 마세요.

이 폴더에서 먼저 명령 전송 없이 확인합니다. 실제 센서와 Serial 연결은 필요합니다.

```bash
python3 src/main_runner.py --serial-port /dev/ttyUSB0 --dry-run
```

팬까지 실행하려면 `--dry-run`을 제거합니다. Ctrl+C 종료 시 기존 요청대로 0/0 정지 명령을 전송합니다. 화재 대응 안전 정지 정책을 의미하지 않으며, 통합 시스템에서는 최종 제어권자가 종료 동작을 결정해야 합니다.

```bash
python3 src/main_runner.py --serial-port /dev/ttyUSB0
```

기본 DB는 이 폴더 기준 `runtime/edge_events.db`입니다. 다른 DB를 쓰려면 `--database /절대경로/edge_events.db`를 지정합니다. 대시보드도 같은 DB를 읽어야 합니다. 지원 옵션은 `--serial-port`, `--database`, `--dry-run` 세 가지입니다. 통신속도는 115200입니다. 기존 오래된 DB보다 새 DB로 시작하는 것을 권장합니다.

## 고정 설정과 변경 범위

최근 8개 샘플, 1/3/5초 예측, 26°C 상한, 28°C 비상 한계, 1초 주기, 최소 PWM35, 보조 ON을 유지합니다. 일반 변경은 10% 목적함수 개선·5회 확인·15초 유지 조건을 사용합니다. 센서 5초 지연 시 35/35 정책도 기존과 같습니다. 이 정책이 모든 환경에서 안전하다는 보장은 없습니다.

설정은 `temperature_controller.py`의 `ControlConfig` 및 제어 객체 초기화에 모았습니다. 입력 샘플 길이와 예측 시간은 모델 규격이므로 임의 변경하지 않습니다. `.pt` 해시는 `ed0adbdb8ee35a3703bfc0bba4cd1d441e2c454233f6b3ff5e14ff516ee418f6`입니다.

직전 변경의 RPM 기반 전력식을 사용합니다. 이번 10/21 DB 분석의 실행 정책은 그 이전 PWM³ 방식이므로, 전달본의 후보 선택이 해당 실험과 완전히 동일하다고 주장하지 않습니다. 전달본 정리는 최신 개발 코드의 v6 판단과 비교 검증하며 재학습하지 않습니다.

## 팀원 메인에 병합

`TemperatureController`는 장치나 DB를 직접 조작하지 않습니다. `observe()`는 센서 수신 스레드에서 호출하고, `decide()`와 `confirm_applied()`는 **하나의 제어 스레드**에서만 호출합니다.

팀원 프로젝트의 동일한 `src`에 필요한 모듈을 병합하거나 이 전달본의 `src`를 Python import 경로에 추가합니다. 팀원에게 이미 센서/팬 드라이버가 있다면 덮어쓰지 말고 아래 API에 연결합니다. 새 Serial 포트를 추가로 열지 마세요. 잘못된 센서값은 `observe()`가 ValueError로 거부하므로 통합 수신 콜백에서도 처리해야 합니다.

```python
from pathlib import Path
from temperature_controller import TemperatureController

temperature = TemperatureController(Path('models/lstm_temperature_trajectory_v6.pt'))

# 기존 센서 수신 콜백에서 실행. SensorSample의 timestamp는 Jetson time.time() 기준 초입니다.
temperature.observe(sample)

# 통합 메인의 1초 제어 주기에서 실행합니다.
decision = temperature.decide()
if decision is not None:
    sample_used, result, reason = decision
    temperature_pwm = (result.intake_pwm, result.exhaust_pwm)
    # 이 지점에서 팀원 연기 판단과 중재합니다. 연기 상태만으로 환기 정책을 임의 결정하지 마세요.
    final_pwm = temperature_pwm  # 실제 병합 시 합의된 통합 정책으로 교체
    fan_driver.apply(*final_pwm, reason)
    temperature.confirm_applied(*final_pwm)
```

`confirm_applied()`에는 온도 제안이 아니라 **중재 후 실제 전송한 PWM**을 전달합니다. 전송 실패 시 확인 호출을 하지 않습니다. 실제 RPM/PWM은 여전히 ESP32 피드백을 사용합니다. 통합 중재자가 35/60/100 조합 밖의 PWM을 쓸 경우 그 조합의 예측/제어는 이번 전달본에서 검증되지 않았습니다.

비전 스레드는 연기 감지 결과·측정 시각만 전달하고 팬을 직접 조작하지 않는 구조로 합칩니다. 여기에는 연기/화재 대응 정책이나 비전 코드를 추가하지 않았습니다. 포트는 `SerialSensorReceiver` 한 객체만 열고 `receiver.send_command`를 `EspFanController.bind_serial_sender()`에 연결합니다. 다른 Serial 객체로 같은 ESP32 포트를 중복 개방하면 안 됩니다.

독립 실행 `main_runner.py`와 팀원 통합 메인을 동시에 실행하지 않습니다. 독립 메인을 사용하지 않고 온도 판단 API만 가져오면 팬 전송·DB·종료 처리는 팀원 통합 메인에서 맡아야 합니다. 통합 판단 결과와 최종 적용값을 구분해 기록하세요.

## DB/전력 연결

기존 대시보드가 사용하는 `sensor_readings`, `control_status`, `control_events_v2`, `runtime_metadata`를 유지합니다. 시험용 `closed_loop_trial_*` 표는 새로 만들지 않습니다. 모델이 아닌 보조/비상 판단은 `model_name`으로 구분합니다. dry-run의 `command_sent`는 false로 기록합니다.

추정 전력은 `5×0.27×(RPM/7500)^3 W`를 두 팬에 합산합니다. 후보는 측정 중앙 RPM 표를 쓰고 현재 전력은 실제 RPM을 씁니다. 후보 JSON에 `estimated_power_w`, `predicted_recovery_energy_wh`, `power_estimation_method`가 기록됩니다. 전력과 에너지는 실측값이 아닙니다.

새 대시보드에는 이 폴더의 `src`를 `--temperature-src /경로/handoff_v6/src`로 연결할 수 있습니다. 대시보드는 본 전달본에 포함하지 않았습니다.

## 정리본 검증

기존 v6 실장 기록 301개를 입력해 **현재 개발본(RPM 기반 전력 정책)의 재생 결과**와 전달본의 PWM 및 예측값이 일치함을 확인했습니다. DB 센서/제어 기록 저장 및 dry-run 미전송 플래그도 확인했습니다. 과거 실장 당시 정책은 다르므로 당시 실제 명령과 완전히 일치한다는 의미는 아닙니다. 새 전달본을 Jetson·비전과 함께 실행하는 실장 검증은 아직 수행하지 않았습니다.
