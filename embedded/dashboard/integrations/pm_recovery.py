"""Record PM events from fresh shared telemetry, independently of fan demands."""
import math
import json

from event_recorder import EventRecorder, QueueSink


class PMRecovery:
    def __init__(self, path, device_id, photos=None, category='pm25'):
        self.sink = QueueSink(path)
        self.category = category
        previous = self.sink.get_state()
        self.sink.db.execute('CREATE TABLE IF NOT EXISTS recovery_status (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.sink.db.commit()
        self.events = {row['event_id']: row for (payload,) in self.sink.db.execute(
            'SELECT payload FROM recovery_status ORDER BY rowid') for row in [json.loads(payload)]}
        temperature = category == 'temperature'
        self.recorder = EventRecorder(self.sink, device_id, stale_seconds=2.5, photos=photos,
            category=category, sensor_key=category, pm_limit=26 if temperature else 50,
            hold_seconds=10 if temperature else 3, median_window=1 if temperature else 3,
            recovery_strict=temperature,
            config={'source': 'integrated_control', 'pwm_source': 'device_feedback',
                    'policy': 'per_fan_max', 'max_sample_gap_seconds': 2.5})
        self.last_timestamp = None
        if previous:
            previous.update(status='interrupted', recording_ended_at=previous.get('last_sample_at') or previous['started_at'])
            self._remember(previous)

    def _remember(self, row):
        event = {key: row.get(key) for key in ('event_id', 'device_id', 'status',
            'started_at', 'first_reached_at', 'recovered_at', 'recording_ended_at', 'recovery_seconds')}
        event['category'] = self.category
        for key in ('started_at', 'first_reached_at', 'recovered_at', 'recording_ended_at'):
            if event[key]: event[key] = event[key].replace(' ', 'T') + 'Z'
        if self.events.get(event['event_id']) == event:
            return
        self.events[event['event_id']] = event
        self.sink.db.execute('INSERT OR REPLACE INTO recovery_status VALUES (?, ?)',
            (event['event_id'], json.dumps(event)))
        while len(self.events) > 100:
            oldest = next(iter(self.events))
            del self.events[oldest]
            self.sink.db.execute('DELETE FROM recovery_status WHERE event_id=?', (oldest,))
        self.sink.db.commit()

    def snapshot(self):
        """Actual recorder decisions, independent of the asynchronous DB uploader."""
        return list(self.events.values())

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
        completed = self.recorder.update(timestamp, pm, *pair, telemetry=row)
        if completed or self.recorder.ev:
            self._remember(completed or self.recorder._event_row())

    def close(self):
        try:
            active = self.recorder._event_row() if self.recorder.ev else None
            self.recorder.close()
            if active:
                active['status'] = 'interrupted'
                self._remember(active)
        finally:
            self.sink.close()
