# 통합 메인 연결

`TemperatureController`는 장치나 DB를 직접 조작하지 않습니다.
`observe(sample)`는 센서 수신 콜백에서, `decide()`와 `confirm_applied()`는 하나의 제어 스레드에서 호출합니다.

```python
from pathlib import Path
from temperature_controller import TemperatureController

temperature = TemperatureController(Path('AI/temperature/models/lstm_temperature_trajectory_v6.pt'))
temperature.observe(sample)  # SensorSample, timestamp는 Jetson time.time() 기준 초
decision = temperature.decide()  # 통합 메인의 1초 주기
if decision is not None:
    sample_used, result, reason = decision
    proposal = (result.intake_pwm, result.exhaust_pwm)
    # 연기 제안과 합의된 우선순위로 중재하고 final_pwm을 실제 전송한다.
    # 전송 성공 후에만 temperature.confirm_applied(*final_pwm)를 호출한다.
```

시리얼 포트와 최종 팬 전송은 각각 한 소유자만 둡니다. 독립 main_runner와 팀원
통합 메인을 동시에 실행하지 않습니다. 실제 팬 드라이버가 이미 있으면 덮어쓰지 않습니다.
전송 실패 시 confirm_applied를 호출하지 않습니다. 제안·최종 명령·실제 PWM/RPM은 구분해 기록합니다.
통합 메인에 판단 API만 가져오면 DB·송신·종료 처리 역시 통합 메인이 담당합니다.

최근 8표본, 1·3·5초 예측, 26°C 상한, 28°C 비상 한계, 최소 PWM35, 보조 ON입니다.
일반 변경은 10% 초과 개선·연속 5회 확인·15초 유지, 위험 시 대기 우회로 구성합니다.
온도 timeout 5초 후 35/35와 종료 0/0은 기존 정책이며 연기 모듈과 단절·종료 정책을
합의해야 합니다. 안전성이 검증된 fallback을 의미하지 않습니다.
이 정리본의 Jetson·비전 동시 실행 검증은 아직 수행하지 않았습니다.
