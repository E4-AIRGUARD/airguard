"""대시보드와 호환되는 센서·제어 SQLite 기록입니다."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

from fan_power import POWER_METHOD, recovery_energy_wh


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


class EventStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        with self.connection:
            self.connection.execute('CREATE TABLE IF NOT EXISTS sensor_readings '
                '(timestamp REAL PRIMARY KEY, temperature_c REAL NOT NULL, humidity_pct REAL, '
                'pm25_ug_m3 REAL, heat_load INTEGER, intake_rpm REAL, exhaust_rpm REAL, intake_pwm REAL, '
                'exhaust_pwm REAL, smoothed_temperature_c REAL)')
            self.connection.execute('CREATE TABLE IF NOT EXISTS control_events_v2 '
                '(timestamp REAL, temperature_c REAL, predicted_temp_c REAL, intake_percent INTEGER, '
                'exhaust_percent INTEGER, recovery_seconds INTEGER, reason TEXT)')
            self.connection.execute('CREATE TABLE IF NOT EXISTS control_status '
                '(id INTEGER PRIMARY KEY CHECK(id=1), timestamp REAL, temperature_c REAL, predicted_temp_c REAL, '
                'intake_percent INTEGER, exhaust_percent INTEGER, recovery_seconds INTEGER, model_name TEXT, '
                'reason TEXT, candidate_json TEXT, policy_mode TEXT, policy_limit_c REAL, last_command_at REAL, command_sent INTEGER)')
            self.connection.execute('CREATE TABLE IF NOT EXISTS runtime_metadata '
                '(timestamp REAL PRIMARY KEY, trial_id TEXT, metadata_json TEXT NOT NULL)')
        # 기존 DB에는 최소 컬럼만 보완하며 기록을 삭제하지 않습니다.
        columns = {r[1] for r in self.connection.execute('PRAGMA table_info(sensor_readings)')}
        if 'smoothed_temperature_c' not in columns:
            with self.connection:
                self.connection.execute('ALTER TABLE sensor_readings ADD COLUMN smoothed_temperature_c REAL')

    def metadata(self, controller, dry_run):
        model = controller.model
        data = dict(model_path=model.model_path, sha256=model.model_sha256, horizon_seconds=5,
            window_size=8, control_policy_resolved='v6', raw_rise_assist=True,
            power_estimation_method=POWER_METHOD, dry_run=dry_run)
        with self.lock, self.connection:
            self.connection.execute('INSERT INTO runtime_metadata VALUES (?, NULL, ?)',
                                    (time.time(), json.dumps(data)))
        print('[MODEL] ' + json.dumps(data, ensure_ascii=False))

    def sensor(self, sample, smoothed):
        values = (sample.timestamp, sample.temperature_c, sample.humidity_pct, sample.pm25_ug_m3,
                  sample.heat_load, sample.intake_rpm, sample.exhaust_rpm, sample.intake_pwm,
                  sample.exhaust_pwm, smoothed)
        with self.lock, self.connection:
            self.connection.execute('INSERT OR REPLACE INTO sensor_readings '
                '(timestamp,temperature_c,humidity_pct,pm25_ug_m3,heat_load,intake_rpm,exhaust_rpm,'
                'intake_pwm,exhaust_pwm,smoothed_temperature_c) VALUES (?,?,?,?,?,?,?,?,?,?)', values)

    def decision(self, sample, result, reason, changed, sent, last_command_at):
        candidates = []
        for item in result.candidate_comparison:
            row = asdict(item)
            row.update(estimated_power_w=item.relative_energy,
                predicted_recovery_energy_wh=recovery_energy_wh(item.relative_energy, item.recovery_seconds),
                power_estimation_method=POWER_METHOD)
            candidates.append(json_value(row))
        prediction = result.predicted_temp_c if math.isfinite(result.predicted_temp_c) else None
        with self.lock, self.connection:
            if changed:
                self.connection.execute('INSERT INTO control_events_v2 VALUES (?,?,?,?,?,?,?)',
                    (sample.timestamp, sample.temperature_c, prediction, result.intake_pwm,
                     result.exhaust_pwm, result.recovery_seconds, reason))
            self.connection.execute('INSERT OR REPLACE INTO control_status '
                '(id,timestamp,temperature_c,predicted_temp_c,intake_percent,exhaust_percent,recovery_seconds,'
                'model_name,reason,candidate_json,policy_mode,policy_limit_c,last_command_at,command_sent) '
                'VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (sample.timestamp, sample.temperature_c, prediction, result.intake_pwm, result.exhaust_pwm,
                 result.recovery_seconds, result.model_name, reason, json.dumps(candidates, allow_nan=False),
                 result.policy_mode, result.policy_limit_c, last_command_at, int(sent)))

    def close(self):
        self.connection.close()
