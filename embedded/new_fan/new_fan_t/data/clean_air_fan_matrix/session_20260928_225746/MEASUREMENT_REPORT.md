# Phase0 + 가열·연기 없는 팬 9조합 측정 결과

- 시작: 2026-09-28T22:57:46
- 종료: 2026-09-28T23:48:53
- 각 구간 300초, 총 센서 측정 50분. 준비·전환 시간은 별도.
- 총 센서 3000개 샘플 / 사진 3010장
- 전체 파일 검증: 통과

| 구간 | 흡기/배기 PWM | 센서 샘플 | 사진 | 파일 검증 |
|---|---|---:|---:|---|
| phase0_baseline | 0/0 | 300 | 301 | 통과 |
| intake_weak_exhaust_weak | 35/35 | 300 | 301 | 통과 |
| intake_weak_exhaust_medium | 35/60 | 300 | 301 | 통과 |
| intake_weak_exhaust_strong | 35/100 | 300 | 301 | 통과 |
| intake_medium_exhaust_weak | 60/35 | 300 | 301 | 통과 |
| intake_medium_exhaust_medium | 60/60 | 300 | 301 | 통과 |
| intake_medium_exhaust_strong | 60/100 | 300 | 301 | 통과 |
| intake_strong_exhaust_weak | 100/35 | 300 | 301 | 통과 |
| intake_strong_exhaust_medium | 100/60 | 300 | 301 | 통과 |
| intake_strong_exhaust_strong | 100/100 | 300 | 301 | 통과 |

검증 항목: CSV 9컬럼·숫자 유효성·Heat_Load=0·설정 PWM 일치, 사진 목록과 실제 파일 일치, 모든 JPG 1280×720 디코딩.

사진은 약 1초 간격이며 첫 프레임과 종료 직전/직후 프레임이 포함되어 구간당 약 301장이다. 센서와 사진은 PC 수신 시각으로 연결하며 하드웨어 동기화는 아니다.
팬 가속 초기 구간도 원본에 포함했다. 센서 교정 정확도나 실제 유량·차압을 이 파일 검증으로 판정하지 않는다.

각 사진 폴더의 run.json에 대응 CSV 경로가 있으며, images.jsonl에는 이미지별 시각이 기록되어 있다.

종료 후 최신 5개 DATA 샘플에서 흡기·배기 PWM=0, RPM=0을 확인했다. 정지 확인 기록은 `stop_verification.json`에 보관했다.
