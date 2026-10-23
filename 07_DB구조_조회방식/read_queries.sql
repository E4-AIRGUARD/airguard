-- AIRGUARD 읽기 전용 조회 예제 (MySQL)
-- SET은 현재 세션 변수만 설정한다. 운영 데이터를 변경하지 않는다.
-- 장치 ID와 이벤트 UUID는 조회 대상에 맞게 입력한다.
SET @device_id = 'airguard-01';
SET @event_id = 'REPLACE_WITH_EVENT_UUID';
-- KST 2026-10-21 하루에 대응하는 UTC 구간
SET @start_utc = '2026-10-20 15:00:00';
SET @end_utc = '2026-10-21 15:00:00';

-- 1. 실제 스키마 확인
SHOW CREATE TABLE e4_recovery_events;
SHOW CREATE TABLE e4_recovery_samples;
SHOW CREATE TABLE e4_recovery_photos;

-- 2. 해당 날짜에 시작한 이벤트 (대시보드 목록과 같은 기간 기준)
SELECT event_id, device_id, started_at, first_reached_at, recovered_at,
       recording_ended_at, status, recovery_seconds, config_json
FROM e4_recovery_events
WHERE device_id = @device_id
  AND started_at >= @start_utc AND started_at < @end_utc
ORDER BY started_at DESC LIMIT 500;

-- 3. 이벤트 상세
SELECT * FROM e4_recovery_events
WHERE device_id = @device_id AND event_id = @event_id;

-- 4. 이벤트 센서 기록
SELECT s.sample_id, s.measured_at, s.temperature, s.humidity, s.pm25,
       s.intake_pwm, s.exhaust_pwm, s.intake_rpm, s.exhaust_rpm,
       s.phase, s.valid_json
FROM e4_recovery_samples AS s
JOIN e4_recovery_events AS e ON e.event_id = s.event_id
WHERE e.device_id = @device_id AND e.event_id = @event_id
ORDER BY s.measured_at;

-- 5. 사진 메타데이터 (원본 JPEG는 Jetson에 있음)
SELECT p.photo_id, p.captured_at, p.stage, p.relative_path
FROM e4_recovery_photos AS p
JOIN e4_recovery_events AS e ON e.event_id = p.event_id
WHERE e.device_id = @device_id AND e.event_id = @event_id
ORDER BY p.captured_at;

-- 6. 회복 완료 이벤트 통계. 진행·중단 이벤트는 제외.
-- 서로 다른 조건의 평균이므로 팬 최적 조건의 증거로 단독 사용하지 않는다.
SELECT COUNT(*) AS completed_events,
       AVG(recovery_seconds) AS avg_recovery_seconds,
       MIN(recovery_seconds) AS min_recovery_seconds,
       MAX(recovery_seconds) AS max_recovery_seconds
FROM e4_recovery_events
WHERE device_id = @device_id
  AND started_at >= @start_utc AND started_at < @end_utc
  AND status = 'completed' AND recovery_seconds IS NOT NULL;

-- 7. 날짜와 기록 구간이 겹치는 이벤트 (전날 시작 이벤트 포함)
SELECT event_id, started_at, recording_ended_at, status
FROM e4_recovery_events
WHERE device_id = @device_id AND started_at < @end_utc
  AND (recording_ended_at IS NULL OR recording_ended_at >= @start_utc)
ORDER BY started_at DESC LIMIT 500;
