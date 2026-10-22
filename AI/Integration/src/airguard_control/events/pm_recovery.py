"""Record PM events from fresh shared telemetry, independently of fan demands."""
import math

from airguard_control.events.event_recorder import EventRecorder, QueueSink


class PMRecovery:
    def __init__(self, path, device_id, photos=None, category='pm25'):
        self.sink = QueueSink(path)
        self.category = category
        temperature = category == 'temperature'
        self.recorder = EventRecorder(self.sink, device_id, stale_seconds=2.5, photos=photos,
            category=category, sensor_key=category, pm_limit=26 if temperature else 50,
            hold_seconds=10 if temperature else 3, median_window=1 if temperature else 3,
            recovery_strict=temperature,
            config={'source': 'integrated_control', 'pwm_source': 'device_feedback',
                    'policy': 'per_fan_max', 'max_sample_gap_seconds': 2.5})
        self.last_timestamp = None

    def observe(self, data, result):
        row = data.get('sensor') or {}
        timestamp = row.get('timestamp')
        if not result.get('sensorFresh'):
            self.recorder.invalidate()
            return
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            return
        self.last_timestamp = timestamp
        pair = (row.get('intake_pwm'), row.get('exhaust_pwm'))
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 100 for v in pair):
            self.recorder.invalidate()
            return
        if self.category == 'temperature':
            pm = row.get('temperature_c') if row.get('bme_ok', True) else None
        else:
            pm = row.get('pm25_ug_m3') if row.get('pms_ok', True) else None
        self.recorder.update(timestamp, pm, *pair, telemetry=row)

    def close(self):
        try:
            self.recorder.close()
        finally:
            self.sink.close()
