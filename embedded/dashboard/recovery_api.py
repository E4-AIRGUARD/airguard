"""Read-only Aiven recovery history, compatible with both supplied schemas."""
from contextlib import contextmanager
import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import json
import math
from pathlib import Path
import tempfile
import uuid
import zipfile
from urllib.request import urlopen

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
EVENT_FIELDS = ('event_id', 'device_id', 'started_at', 'first_reached_at', 'recovered_at',
                'recording_ended_at', 'status', 'recovery_seconds', 'config_json')
SAMPLE_FIELDS = ('sample_id', 'measured_at', 'temperature', 'humidity', 'gas', 'gas_unit',
                 'pm25', 'intake_pwm', 'exhaust_pwm', 'heat_load', 'exhaust_rpm',
                 'intake_rpm', 'valid_json', 'phase')
CATEGORIES = {'all', 'pm25', 'temperature', 'smoke', 'combined', 'unknown'}
STATUSES = {'all', 'active', 'completed', 'interrupted', 'post'}


def json_object(value):
    if isinstance(value, dict): return value
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError): return {}


def iso(value):
    if value is None: return None
    if isinstance(value, str): value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None: value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def number(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def event_category(row, config):
    if config.get('source') == 'camera_smoke_detector': return 'smoke', 'recorded'
    raw = row.get('category') or row.get('event_type') or config.get('category') or config.get('event_type')
    names = {'smoke': 'pm25', 'pm25': 'pm25', 'temperature': 'temperature', 'heat': 'temperature',
             'both': 'combined', 'combined': 'combined', 'mixed': 'combined'}
    if raw in names: return names[raw], 'recorded'
    thresholds = json_object(config.get('thresholds'))
    keys = set(thresholds) & {'temperature', 'pm25'}
    if keys == {'temperature', 'pm25'}: return 'combined', 'configured_thresholds'
    if keys: return next(iter(keys)), 'configured_thresholds'
    if config.get('pm_limit') is not None: return 'pm25', 'configured_thresholds'
    return 'unknown', 'unknown'


def public_event(row):
    config = json_object(row.get('config_json'))
    category, basis = event_category(row, config)
    result = {key: row.get(key) for key in ('event_id', 'device_id', 'status')}
    for key in ('started_at', 'first_reached_at', 'recovered_at', 'recording_ended_at'):
        result[key] = iso(row.get(key))
    result.update(category=category, categoryBasis=basis, recovery_seconds=number(row.get('recovery_seconds')))
    result['criteria'] = {key: config[key] for key in
        ('thresholds', 'pm_limit', 'hold_seconds', 'pre_seconds', 'post_seconds', 'max_gap_seconds', 'max_sample_gap_seconds', 'recovery_operator')
        if key in config}
    return result


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value.lower(): raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError): raise ValueError('Invalid record ID') from None


def csv_cell(value):
    if value is None: return ''
    if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')): return "'" + value
    return value


class RecoveryRepository:
    def __init__(self, connect_fn, photo_root, photo_api=None):
        self.connect_fn = connect_fn
        self.photo_root = Path(photo_root).resolve()
        self.photo_api = photo_api.rstrip('/') if photo_api else None

    @contextmanager
    def session(self):
        conn = self.connect_fn()
        try:
            with conn.cursor() as cur: yield cur
        finally:
            try: conn.rollback()
            finally: conn.close()

    @staticmethod
    def columns(cur, table):
        # Table names are internal constants; never passed by an HTTP caller.
        cur.execute('SHOW COLUMNS FROM ' + table)
        return {r['Field'] for r in cur.fetchall()}

    def event_fields(self, cur):
        columns = self.columns(cur, 'e4_recovery_events')
        return ','.join(EVENT_FIELDS + tuple(k for k in ('event_type', 'category') if k in columns))

    def list_events(self, date_value=None, category='all', status='all', page=1):
        if category not in CATEGORIES or status not in STATUSES or not 1 <= page <= 1000:
            raise ValueError('Invalid filter')
        selected = date.fromisoformat(date_value) if date_value else None
        with self.session() as cur:
            fields = self.event_fields(cur)
            cur.execute('SELECT MAX(started_at) AS latest FROM e4_recovery_events')
            latest = cur.fetchone()['latest']
            latest_date = datetime.fromisoformat(iso(latest).replace('Z', '+00:00')).astimezone(KST).date() if latest else None
            selected = selected or latest_date or datetime.now(KST).date()
            start = datetime.combine(selected, time(), KST).astimezone(UTC).replace(tzinfo=None)
            end = start + timedelta(days=1)
            cur.execute('SELECT '+fields+' FROM e4_recovery_events WHERE started_at >= %s AND started_at < %s '
                        'ORDER BY started_at DESC,event_id DESC LIMIT 5001', (start, end))
            rows = cur.fetchall()
        truncated = len(rows) > 5000
        events = [public_event(row) for row in rows[:5000]]
        events = [e for e in events if (category == 'all' or e['category'] == category)
                  and (status == 'all' or e['status'] == status)]
        pages = max(1, (len(events)+9)//10)
        page = min(page, pages)
        return dict(available=True, source='Aiven MySQL', date=selected.isoformat(), timezone='Asia/Seoul',
            latestDate=latest_date.isoformat() if latest_date else None, page=page, pages=pages,
            total=len(events), truncated=truncated, events=events[(page-1)*10:page*10])

    def get_event(self, cur, event_id):
        fields = self.event_fields(cur)
        cur.execute('SELECT '+fields+' FROM e4_recovery_events WHERE event_id=%s', (identifier(event_id),))
        row = cur.fetchone()
        if not row: raise LookupError('Event not found')
        return public_event(row)

    def sample_fields(self, cur):
        columns = self.columns(cur, 'e4_recovery_samples')
        return ','.join(k if k in columns else 'NULL AS '+k for k in SAMPLE_FIELDS)

    @staticmethod
    def public_sample(row):
        valid = json_object(row.get('valid_json'))
        result = dict(sample_id=row['sample_id'], measured_at=iso(row['measured_at']),
                      phase=row.get('phase'), gas_unit=row.get('gas_unit'))
        for key in ('temperature', 'humidity', 'gas', 'pm25', 'intake_pwm', 'exhaust_pwm', 'heat_load', 'intake_rpm', 'exhaust_rpm'):
            result[key] = None if valid.get(key) is False else number(row.get(key))
        result['valid'] = valid
        return result

    def local_photo(self, row):
        relative = str(row.get('relative_path') or '').replace('\\', '/')
        path = (self.photo_root / relative).resolve()
        expected = (self.photo_root / 'photos' / identifier(row['event_id'])).resolve()
        if not path.is_relative_to(expected) or path.suffix.lower() not in ('.jpg', '.jpeg'):
            return None
        return path if path.is_file() else None

    def detail(self, event_id):
        identifier(event_id)
        with self.session() as cur:
            event = self.get_event(cur, event_id)
            fields = self.sample_fields(cur)
            cur.execute('SELECT '+fields+' FROM e4_recovery_samples WHERE event_id=%s ORDER BY measured_at,sample_id LIMIT 20001', (event_id,))
            rows = cur.fetchall()
            cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='e4_recovery_photos'")
            photos, photo_count = [], 0
            if cur.fetchone():
                cur.execute('SELECT COUNT(*) AS total FROM e4_recovery_photos WHERE event_id=%s', (event_id,))
                photo_count = cur.fetchone()['total']
                cur.execute('SELECT photo_id,event_id,captured_at,stage,relative_path FROM e4_recovery_photos WHERE event_id=%s ORDER BY captured_at,photo_id LIMIT 6', (event_id,))
                photos = cur.fetchall()
        return dict(available=True, event=event, samples=[self.public_sample(r) for r in rows[:20000]],
            samplesTruncated=len(rows)>20000, photoCount=photo_count,
            photos=[dict(photo_id=p['photo_id'], captured_at=iso(p['captured_at']), stage=p['stage'],
                available=self.local_photo(p) is not None or bool(self.photo_api),
                url='/api/recovery/photos/'+identifier(p['photo_id'])) for p in photos])

    def photo(self, photo_id):
        with self.session() as cur:
            cur.execute('SELECT photo_id,event_id,relative_path FROM e4_recovery_photos WHERE photo_id=%s', (identifier(photo_id),))
            row = cur.fetchone()
        return self.read_photo(row)

    def read_photo(self, row):
        path = self.local_photo(row) if row else None
        if path is not None:
            if path.stat().st_size > 10*1024*1024: raise LookupError('Photo not available')
            raw = path.read_bytes()
        elif row and self.photo_api:
            try:
                with urlopen(self.photo_api+'/api/recovery-photo/'+identifier(row['event_id'])+'/'+identifier(row['photo_id']),timeout=5) as response:
                    raw=response.read(10*1024*1024+1)
            except OSError: raise LookupError('Photo not available') from None
            if len(raw)>10*1024*1024: raise LookupError('Photo too large')
        else: raise LookupError('Photo not available')
        if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'): raise LookupError('Invalid JPEG')
        return raw

    def export_photos(self, event_id):
        identifier(event_id)
        with self.session() as cur:
            self.get_event(cur, event_id)
            cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='e4_recovery_photos'")
            if not cur.fetchone(): raise LookupError('No photos')
            # The archive includes every photo, independently of the six-image preview.
            cur.execute('SELECT photo_id,event_id,captured_at,relative_path FROM e4_recovery_photos WHERE event_id=%s ORDER BY captured_at,photo_id', (event_id,))
            photos = cur.fetchall()
        if not photos: raise LookupError('No photos')
        archive = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
                for index, photo in enumerate(photos, 1):
                    # UUID names prevent paths or untrusted metadata becoming ZIP paths.
                    filename = f'{index:04d}_{identifier(photo["photo_id"])}.jpg'
                    bundle.writestr(filename, self.read_photo(photo))
            archive.seek(0)
            return archive
        except Exception:
            archive.close()
            raise  # Never label a partial archive as all photos.

    def export_csv(self, event_id):
        identifier(event_id)
        with self.session() as cur:
            self.get_event(cur, event_id)
            fields = self.sample_fields(cur)
            cur.execute('SELECT '+fields+' FROM e4_recovery_samples WHERE event_id=%s ORDER BY measured_at,sample_id', (event_id,))
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow(['시각 (KST)', 'PM2.5', '온도', '습도', '흡기/배기 PWM', '흡기/배기 RPM', '구간'])
            def fan_pair(sample, metric):
                return ' / '.join('—' if sample[key] is None else str(sample[key]).removesuffix('.0')
                                  for key in ('intake_'+metric, 'exhaust_'+metric))
            while True:
                rows = cur.fetchmany(500)
                if not rows: break
                for row in rows:
                    sample = self.public_sample(row)
                    kst = datetime.fromisoformat(sample['measured_at'].replace('Z', '+00:00')).astimezone(KST).isoformat()
                    writer.writerow([csv_cell(v) for v in [kst, sample['pm25'], sample['temperature'],
                        sample['humidity'], fan_pair(sample, 'pwm'), fan_pair(sample, 'rpm'), sample['phase']]])
            return ('\ufeff'+out.getvalue()).encode('utf-8')
