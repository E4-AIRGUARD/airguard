"""PM 이벤트 기록기: PM이 기준(기본 50)을 넘어 올라간 순간부터 기준 아래로 회복이 확정될 때까지를 한 이벤트로 로컬 SQLite 대기열에 쌓는다.
저장 형식은 팀 DB 문서(airguard 7_DB구조_조회방식)의 e4_recovery_events / e4_recovery_samples 열과 같다. 대기열은 uploader.py가 Aiven MySQL로 올린다.

- 시작: 3표본 중앙값 PM이 pm_limit 초과 -> 이벤트 생성(status=active), started_at = 넘은 시각
- first_reached_at: 시작 뒤 처음 PM이 pm_limit 이하가 된 시각 (재상승해도 유지)
- 회복 확정: PM이 pm_limit 이하를 hold_seconds 동안 유지 -> status=completed, recovered_at = 확정 시각, recovery_seconds = recovered_at - started_at
  (문서의 'hold_seconds 유지' 규칙. 유지 구간 샘플도 기록에 포함, 마지막 샘플 phase=post)
- 센서 값이 stale_seconds 넘게 없으면 유지시간을 초기화. 프로그램 종료 시 진행 중이면 status=interrupted
- category: 이벤트 항목 구분(기본 'pm25'). config_json의 category에도 같이 들어가서 DB 구조를 안 바꿔도 조회 가능, 별도 열을 추가하면 그 열로도 저장
- 샘플: 이벤트 중 매 호출(기본 1초)마다 measured_at(UTC), pm25, 적용 intake/exhaust PWM, valid_json
  통합 경로는 telemetry로 온도·습도·RPM을 제공한다. 제공되지 않거나 유효하지 않은 값과 가스는 NULL.
"""
import json, math, sqlite3, statistics, uuid
from pathlib import Path
from collections import deque
from datetime import datetime, timezone


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


class QueueSink:
    """pending.sqlite3의 queue(id, kind, payload), state(id, payload) 형식(문서 6절)을 따른다."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()

    def put(self, kind, row):
        self.db.execute('INSERT INTO queue(kind, payload) VALUES (?, ?)', (kind, json.dumps(row, ensure_ascii=False, allow_nan=False)))
        self.db.commit()

    def set_state(self, payload):
        self.db.execute('INSERT OR REPLACE INTO state(id, payload) VALUES (1, ?)', (json.dumps(payload) if payload else '',))
        self.db.commit()

    def get_state(self):
        r = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        return json.loads(r[0]) if r and r[0] else None

    def close(self):
        self.db.close()


class EventRecorder:
    def __init__(self, sink, device_id, pm_limit=50.0, hold_seconds=3.0, stale_seconds=10.0, config=None, category='pm25', sensor_key='pm25', median_window=3, recovery_strict=False, photos=None):
        self.sink, self.device_id = sink, device_id
        self.pm_limit, self.hold_seconds, self.stale_seconds = pm_limit, hold_seconds, stale_seconds
        self.category = category   # 이상/회복 페이지의 항목 구분: 'pm25'(연기) 또는 'temperature'(온도)
        self.sensor_key, self.recovery_strict, self.photos = sensor_key, recovery_strict, photos
        self.config = dict(config or {}, pm_limit=pm_limit, hold_seconds=hold_seconds, category=category)
        self.config.setdefault('source', 'smoke_fan')
        self.config.update(thresholds={sensor_key:pm_limit}, recovery_operator='<' if recovery_strict else '<=')
        if sensor_key != 'pm25': self.config.pop('pm_limit', None)
        self.buf = deque(maxlen=median_window)
        self.ev = None            # 진행 중 이벤트(dict)
        self.below_since = None
        self.last_t = None
        self.last_update = None
        prev = sink.get_state()   # 이전 실행이 예고 없이 끝났으면 중단 처리 (문서 5절)
        if prev:
            prev.update(status='interrupted', recording_ended_at=prev.pop('last_sample_at', None) or prev.get('recording_ended_at') or prev['started_at'])
            sink.put('event', prev); sink.set_state(None)

    def _event_row(self):
        return {k: self.ev.get(k) for k in ('event_id', 'device_id', 'started_at', 'first_reached_at', 'recovered_at', 'recording_ended_at', 'status', 'recovery_seconds', 'config_json')} | {'category': self.category}

    def _sample(self, ts, pm, i, e, phase, telemetry=None):
        telemetry = telemetry or {}
        def value(key, valid=True):
            v = telemetry.get(key)
            return v if valid and isinstance(v, (int, float)) and math.isfinite(v) else None
        temperature = value('temperature_c', telemetry.get('bme_ok', True))
        humidity = value('humidity_pct', telemetry.get('bme_ok', True))
        intake_rpm, exhaust_rpm = value('intake_rpm'), value('exhaust_rpm')
        if self.sensor_key != 'pm25': pm = value('pm25_ug_m3', telemetry.get('pms_ok', True))
        self.sink.put('sample', {
            'event_id': self.ev['event_id'], 'sample_id': str(uuid.uuid4()), 'measured_at': _iso(ts),
            'temperature': temperature, 'humidity': humidity, 'gas': None, 'gas_unit': None, 'pm25': pm,
            'intake_pwm': float(i), 'exhaust_pwm': float(e), 'heat_load': None, 'exhaust_rpm': exhaust_rpm, 'intake_rpm': intake_rpm,
            'valid_json': json.dumps({'temperature': temperature is not None, 'humidity': humidity is not None,
                'pm25': pm is not None, 'gas': False, 'intake_rpm': intake_rpm is not None,
                'exhaust_rpm': exhaust_rpm is not None}), 'phase': phase})
        self.sink.set_state(dict(self._event_row(), last_sample_at=_iso(ts)))
        if self.photos:
            stage = 'recovered' if phase == 'post' else 'start' if self.ev['started_at'] == _iso(ts) else 'progress'
            photo = self.photos.capture(self.ev['event_id'], stage, self.ev['started_at']+'+00:00', _iso(ts)+'+00:00')
            if photo: self.sink.put('photo', photo)

    def invalidate(self):
        self.below_since = None
        self.buf.clear()

    def update(self, ts, raw_pm, intake_pwm, exhaust_pwm, telemetry=None):
        """ts: 유닉스 시각(초). 제어 루프에서 매 초 호출. 판단에는 3표본 중앙값을 쓴다."""
        if not isinstance(ts, (int, float)) or not math.isfinite(ts):
            raise ValueError('Finite Unix timestamp required')
        if self.last_update is not None and ts <= self.last_update:
            return None
        self.last_update = ts
        if self.last_t is not None and ts - self.last_t > self.stale_seconds:
            self.invalidate()
        ok = isinstance(raw_pm, (int, float)) and math.isfinite(raw_pm) and (self.sensor_key != 'pm25' or raw_pm >= 0)
        if not ok:
            self.invalidate()
        if ok:
            self.buf.append(raw_pm); self.last_t = ts
        pm = statistics.median(self.buf) if self.buf else None
        stale = self.last_t is None or ts - self.last_t > self.stale_seconds
        if self.ev is None:
            if ok and pm is not None and (pm >= self.pm_limit if self.recovery_strict else pm > self.pm_limit):
                self.ev = dict(event_id=str(uuid.uuid4()), device_id=self.device_id, started_at=_iso(ts), first_reached_at=None,
                               recovered_at=None, recording_ended_at=None, status='active', recovery_seconds=None,
                               config_json=json.dumps(self.config, ensure_ascii=False), _t0=ts)
                self.below_since = None
                self.sink.put('event', self._event_row()); self.sink.set_state(self._event_row())
                self._sample(ts, raw_pm, intake_pwm, exhaust_pwm, 'active', telemetry)
            return None
        if stale or not ok or pm is None:
            self.below_since = None            # 결측은 유지 시간을 초기화
            if ok:
                self._sample(ts, raw_pm, intake_pwm, exhaust_pwm, 'active', telemetry)
            return None
        if (pm < self.pm_limit if self.recovery_strict else pm <= self.pm_limit):
            if self.ev['first_reached_at'] is None:
                self.ev['first_reached_at'] = _iso(ts); self.sink.put('event', self._event_row())
            if self.below_since is None:
                self.below_since = ts
            if ts - self.below_since >= self.hold_seconds:
                self._sample(ts, raw_pm, intake_pwm, exhaust_pwm, 'post', telemetry)
                self.ev.update(status='completed', recovered_at=_iso(ts), recording_ended_at=_iso(ts), recovery_seconds=ts - self.ev['_t0'])
                row = self._event_row(); self.sink.put('event', row); self.sink.set_state(None)
                if self.photos: self.photos.last.pop(self.ev['event_id'], None)
                self.ev = None; self.below_since = None
                return row
        else:
            self.below_since = None
        self._sample(ts, raw_pm, intake_pwm, exhaust_pwm, 'active', telemetry)
        return None

    def close(self, ts=None):
        """프로그램 종료 시 호출. 진행 중 이벤트는 interrupted로 남긴다."""
        if self.ev is not None:
            self.ev.update(status='interrupted', recording_ended_at=_iso(ts) if ts is not None else _iso(datetime.now(timezone.utc).timestamp()))
            self.sink.put('event', self._event_row()); self.sink.set_state(None); self.ev = None
