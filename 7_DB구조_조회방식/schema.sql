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

CREATE TABLE IF NOT EXISTS e4_recovery_photos (
 photo_id CHAR(36) PRIMARY KEY,
 event_id CHAR(36) NOT NULL,
 captured_at DATETIME(6) NOT NULL,
 stage VARCHAR(20) NOT NULL,
 relative_path VARCHAR(255) NOT NULL,
 INDEX idx_e4_photo_time (event_id, captured_at),
 FOREIGN KEY (event_id) REFERENCES e4_recovery_events(event_id)
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
