# 배포 모델

| 파일 | 역할 | 크기 |
| --- | --- | --- |
| `temperature/lstm_temperature_trajectory_v6.pt` | 온도 상대 궤적 LSTM v6 | 29,193바이트 |
| `pm/model.json` | 팬 조합별 PM 감쇠·전력 추정 정보 | 10,401바이트 |

두 파일은 기존 대시보드에서 사용하는 원본을 그대로 복사했다.
원본 경로, 기준 커밋, 크기, SHA-256은 `manifest.json`에 기록했다.
이 폴더의 모델과 src의 제어 코드는 함께 배포한다.

```bash
python3 scripts/check_models.py
```

온도 모델은 최근 8표본을 사용하고 5초 궤적을 예측한다.
구현은 `src/airguard_control/temperature/lstm_controller.py`이며
고정된 v6 해시와 architecture·입력 규격을 확인한 후 모델을 읽는다.
모델을 교체할 때는 코드·manifest·회귀 검사를 함께 갱신한다.

PM 모델은 `src/airguard_control/pm/smoke_fan.py`가 읽는다.
전력·에너지는 RPM 기반 추정이며 전력계 실측이나 이번 변경의 성능 개선 결과가 아니다.
카메라 연기 분류 모델은 별도 영상 서비스에서 관리한다.
