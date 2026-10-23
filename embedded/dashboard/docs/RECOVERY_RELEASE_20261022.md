# 2026-10-22 회복·최근 알림 최종 적용

- PM/온도 EventRecorder의 실제 UUID·상태를 `integratedControl.recoveryEvents`로 전달합니다. 최근 100건씩 로컬 `recovery_status`에 보존합니다.
- 목록은 실시간 회복 확정 신호를 반영합니다. 선택 사건의 DB 상세가 진행 중이면 로딩 화면을 표시하고 응답 후 1초 뒤 다시 조회합니다. 완료 시 중지하며 사건 변경·페이지 이동·오래된 응답을 구분합니다.
- 영상 연기는 기존 카메라 `/api/smoke`의 사건 ID·발생·해제 시각으로 저장합니다. 안정적인 UUID와 `vision_status`로 중복을 방지합니다. `/event/{id}.jpg` 원본 사진도 보관합니다. PM 대기열과 업로더를 공유하되 PM recorder의 `state`를 변경하지 않습니다.
- 영상 사건은 `config_json.source=camera_smoke_detector`, `category=smoke`로 구분합니다. 기존 스키마 및 기존 `smoke`→PM 호환 해석을 유지하면서 새 영상 사건만 별도 분류합니다. 원본 센서 표본을 소급 생성하지 않습니다.
- 카메라 HTTP/DB 보완 처리는 별도 스레드로 실행합니다. 팬 제어 정책·임계값·모델과 카메라 추론 프로세스는 변경하지 않았습니다.
- 최근 알림에 온도·PM 이상/회복과 영상 연기 감지/해제를 함께 표시합니다. 각 센서의 기존 DB 기록도 읽으며 최대 40개를 시간순으로 표시합니다.
- 실측 토스트와 떠 있는 카메라 팝업, 메인의 통합 팬 제어 설명 박스, 현재 CFD 아래 파일명·설명 문구를 제거했습니다. 전체 페이지 이상 알림은 유지합니다.

## 적용·확인

2026-10-22 16:27 KST경 센서 사건이 진행 중이지 않음을 확인한 뒤 기존 Jetson 제어 파일을 백업하고 같은 실행 인수로 재시작했습니다. 센서 `available=true`, `sensorFresh=true`, 제어 `mode=live`, 유효한 dashboard lease를 확인했습니다.

카메라 서버에 남아 있던 영상 사건 13건이 Aiven에 반영되었습니다. 업로드 대기열이 비어 있음을 확인했고, 대시보드 API에서 영상 사건 13건, 진행/완료 사건 각각의 사진 메타데이터와 JPEG 응답을 확인했습니다. 브라우저에서 완료 사건의 시각·사진·ZIP 링크, 통합 최근 알림, CFD 문구 제거를 확인했습니다.

로컬 검증: recovery-live, recovery-history, recovery-trigger, recent-alerts, dashboard, sensor-values, smoke-camera JavaScript 검사; pm_recovery 19건, vision_recovery, recovery_api 13건 통과. PC Aiven 가상환경에는 numpy가 없어 전체 integrated_control 테스트 중 모델 테스트를 실행할 수 없었으며, 해당 실제 모델 경로를 Jetson의 기존 환경에서 하드웨어 출력 없는 shadow 모드로 검증했습니다.

수정/공유 시 [START_HERE_KO.md](../START_HERE_KO.md)를 따릅니다. 개인 접속 정보·실험 DB·로그·사진은 공유 ZIP에 포함하지 않습니다. 카메라 v7 TensorRT 엔진은 대상 장비의 검증된 파일을 별도 사용합니다.
