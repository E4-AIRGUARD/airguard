-- 팀 DB 문서(airguard/7_DB구조_조회방식/schema.sql)에서 e4_recovery_events, e4_recovery_samples만 옮긴 사본. 사진 테이블은 쓰지 않음.
CREATE TABLE IF NOT EXISTS e4_recovery_events (
 event_id CHAR(36) PRIMARY KEY,
 device_id VARCHAR(100) NOT NULL,
 started_at DATETIME(6) NOT NULL,
 first_reached_at DATETIME(6) NULL,
 recovered_at DATETIME(6) NULL,
 recording_ended_at DATETIME(6) NULL,
 status VARCHAR(30) NOT NULL,
 recovery_seconds DOUBLE NULL,
 config_json JSON NOT NULL,
 INDEX idx_e4_started (device_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS e4_recovery_samples (
 event_id CHAR(36) NOT NULL,
 sample_id CHAR(36) NOT NULL,
 measured_at DATETIME(6) NOT NULL,
 temperature DOUBLE NULL,
 humidity DOUBLE NULL,
 gas DOUBLE NULL,
 gas_unit VARCHAR(40) NULL,
 pm25 DOUBLE NULL,
 intake_pwm DOUBLE NOT NULL,
 exhaust_pwm DOUBLE NOT NULL,
 heat_load DOUBLE NULL,
 exhaust_rpm DOUBLE NULL,
 intake_rpm DOUBLE NULL,
 valid_json JSON NOT NULL,
 phase VARCHAR(20) NOT NULL,
 PRIMARY KEY (event_id, sample_id),
 INDEX idx_e4_sample_time (event_id, measured_at),
 FOREIGN KEY (event_id) REFERENCES e4_recovery_events(event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- (선택) 이상/회복 페이지에서 항목(연기/온도)별로 거르려면 열 추가. 추가하면 업로더에 E4_CATEGORY_COLUMN=1
-- 열을 안 추가해도 config_json에 category가 들어 있어 JSON_UNQUOTE(JSON_EXTRACT(config_json, '$.category'))로 조회 가능
-- ALTER TABLE e4_recovery_events ADD COLUMN category VARCHAR(20) NULL, ADD INDEX idx_e4_category (device_id, category, started_at);
