"""Read-only dashboard API. No torch import, Serial access or fan commands."""
import argparse
from datetime import date as Date, datetime, time as Time, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
from pathlib import Path
import re
import shutil
import sqlite3
from threading import Lock
import time
from urllib.parse import parse_qs, unquote, urlsplit

from fan_power import POWER_METHOD, combo_power_w, observed_energy
from temperature_events import temperature_events

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
NUMERIC = {'timestamp', 'temperature_c', 'smoothed_temperature_c', 'humidity_pct',
           'pm25_ug_m3', 'heat_load', 'intake_rpm', 'exhaust_rpm', 'intake_pwm',
           'exhaust_pwm', 'intake_percent', 'exhaust_percent', 'predicted_temp_c',
           'recovery_seconds', 'policy_limit_c', 'last_command_at', 'command_sent'}
TABLES = {'sensor_readings', 'control_status', 'control_events_v2', 'closed_loop_trial_events'}


def clean(value, key=None):
    if isinstance(value, bytes):
        return int.from_bytes(value, 'little', signed=True) if key in NUMERIC and len(value) == 8 else None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def read_rows(connection, table, start=None, end=None, limit=1000, newest=False):
    if table not in TABLES:
        raise ValueError('Unknown table')
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    conditions, parameters = [], []
    for bound, operator in ((start, '>='), (end, '<')):
        if bound is not None:
            conditions.append(f'timestamp {operator} ?')
            parameters.append(bound)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    order = 'DESC' if newest else 'ASC'
    return [clean(dict(row)) for row in connection.execute(
        f'SELECT * FROM {table}{where} ORDER BY timestamp {order} LIMIT ?', (*parameters, limit))]


def recovery_episodes(rows):
    """Sensor rise or T>=26 starts an event; stable readings confirm recovery."""
    return temperature_events(rows)


def legacy_recovery_episodes(rows):
    """Reference-only previous threshold implementation."""
    episodes, current, observed = [], None, []
    previous, low_start, low_count = None, None, 0

    def finish(status, end=None):
        current.update(status=status, end=end,
            duration_seconds=end-current['start'] if end is not None else None,
            last_observed_at=observed[-1]['timestamp'])
        current.update(observed_energy(observed))
        episodes.append(current)

    for row in rows:
        stamp, temperature = row.get('timestamp'), row.get('temperature_c')
        if stamp is None or temperature is None:
            continue
        gap = previous is not None and stamp-previous > 2.5
        if gap:
            if current is not None:
                finish('interrupted')
            current, observed, low_start, low_count = None, [], None, 0
        if temperature >= 26:
            if current is None:
                current = dict(id=f'temperature-{stamp:.6f}', type='temperature', start=stamp,
                    end=None, duration_seconds=None, peak_c=temperature, threshold_c=26.,
                    start_temperature_c=temperature, status='active', left_censored=previous is None or gap)
                observed = []
            current['peak_c'] = max(current['peak_c'], temperature)
            low_start, low_count = None, 0
        elif current is not None:
            if low_count == 0:
                low_start = stamp
            low_count += 1
        if current is not None:
            observed.append(row)
            current['last_temperature_c'] = temperature
            if low_count >= 3 and stamp-low_start >= 2:
                finish('recovered', stamp)
                current, observed, low_start, low_count = None, [], None, 0
        previous = stamp
    if current is not None:
        finish('active')
    return episodes


class SnapshotService:
    def __init__(self, database):
        self.database = Path(database)
        self.lock = Lock()
        self.daily_cache = {}  # Only two dates; daily scans at most once per 5 s per date.

    def snapshot(self, seconds=300, date=None, now=None):
        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, KST).date()
        if not 60 <= seconds <= 3600:
            raise ValueError('seconds must be between 60 and 3600')
        if date is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError('date must be YYYY-MM-DD')
        day = Date.fromisoformat(date) if date else today
        start = datetime.combine(day, Time.min, KST).timestamp()
        payload = dict(api_version='1', available=False, connected=False, server_time=now,
            sensor=None, control=None, readings=[], controls=[], trial_events=[], episodes=[],
            sensor_age_seconds=None, control_age_seconds=None, date=day.isoformat(),
            power_configured=True, power_estimation_method=POWER_METHOD, truncated=False)
        if not self.database.is_file():
            return dict(payload, message='DB 파일 없음: 메인 실행과 DB 경로를 확인하세요.')
        # Serialize snapshots to avoid simultaneous daily scans from several clients.
        with self.lock:
            with sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro', uri=True, timeout=1) as c:
                c.row_factory = sqlite3.Row
                c.execute('PRAGMA query_only=ON')
                c.execute('BEGIN')
                latest = read_rows(c, 'sensor_readings', limit=1, newest=True)
                status = read_rows(c, 'control_status', limit=1, newest=True)
                readings = read_rows(c, 'sensor_readings', now-seconds, now+1, 10000)
                controls = read_rows(c, 'control_events_v2', start, start+86400, 501, True)
                trial = read_rows(c, 'closed_loop_trial_events', start, start+86400, 501, True)
                cached = self.daily_cache.get(day.isoformat())
                daily = None
                if cached is None or time.monotonic()-cached[0] >= 5:
                    daily = read_rows(c, 'sensor_readings', start, start+86400, 100001)
            if daily is not None:
                cached = (time.monotonic(), now, recovery_episodes(daily[:100000]), len(daily)>100000)
                if len(self.daily_cache) >= 2:
                    self.daily_cache.pop(next(iter(self.daily_cache)))
                self.daily_cache[day.isoformat()] = cached
            sensor = latest[0] if latest else None
            control = status[0] if status else None
            for row in readings + ([sensor] if sensor else []):
                row['estimated_power_w'] = combo_power_w(row.get('intake_rpm'), row.get('exhaust_rpm'))
            if control:
                try:
                    control['candidates'] = clean(json.loads(control.get('candidate_json') or '[]'))
                except (TypeError, ValueError):
                    control['candidates'] = []
            age = now-sensor['timestamp'] if sensor else None
            control_age = now-control['timestamp'] if control else None
            episodes = [dict(episode) for episode in cached[2]]
            for episode in episodes:
                if episode['status'] == 'active' and (day != today or age is None or not -2 <= age <= 10 or cached[3]):
                    episode['status'] = 'unconfirmed'
            recent_events = temperature_events(readings)
            active = next((e for e in reversed(recent_events) if e['status'] == 'active'), None)
            connected = age is not None and -2 <= age <= 10
            predicted_risk = (control is not None and control_age is not None and -2 <= control_age <= 10
                and control.get('predicted_temp_c') is not None and control['predicted_temp_c'] >= 26)
            over_limit = sensor is not None and sensor.get('temperature_c') is not None and sensor['temperature_c'] >= 26
            anomaly = connected and (over_limit or active is not None or predicted_risk)
            alert = dict(state='unknown' if not connected else 'critical' if over_limit else 'warning' if anomaly else 'normal',
                is_anomaly=bool(anomaly), label='수신 확인 필요' if not connected else '이상' if anomaly else '정상',
                reason='실제 온도 26°C 이상' if over_limit else '온도 상승 감지' if active else '예측 상한 위험' if predicted_risk else '온도 상승/초과 감지 없음',
                active_event=active if connected else None)
            # Merge the newest active event without waiting for the daily cache refresh.
            if active is not None and connected and day == today and not any(
                    e['status'] == 'active' for e in episodes):
                episodes = [e for e in episodes if e['id'] != active['id']] + [dict(active)]
                episodes.sort(key=lambda e:e['start'])
            return dict(payload, available=True, connected=age is not None and -2 <= age <= 10,
                sensor=sensor, control=control, readings=readings, controls=controls[:500],
                trial_events=trial[:500], episodes=episodes, sensor_age_seconds=age,
                control_age_seconds=control_age, episodes_updated_at=cached[1], temperature_alert=alert,
                truncated=cached[3] or len(controls)>500 or len(trial)>500 or len(readings)>=10000,
                message='센서·제어는 조회마다 갱신. 사건은 저장된 센서에서 재구성: 상승률 감지 또는 26°C 이상 시작, '
                        '<26°C이며 상승률이 안정된 3표본·2초 이상에서 회복 확인. 전력/에너지는 RPM 기반 추정값.')


class ApiHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, service, web_root=None, allow_origin=None, **kwargs):
        self.service, self.web_root, self.allow_origin = service, web_root, allow_origin
        super().__init__(*args, **kwargs)

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == '/api/health':
            return self.send_json(200, dict(status='ok', database_exists=self.service.database.is_file(), read_only=True))
        if request.path != '/api/snapshot':
            return self.serve_static(request.path)
        query = parse_qs(request.query, keep_blank_values=True)
        try:
            if set(query)-{'seconds', 'date'} or any(len(values)!=1 for values in query.values()):
                raise ValueError('Supported query: seconds, date')
            payload = self.service.snapshot(int(query.get('seconds', ['300'])[0]), query.get('date', [None])[0])
        except (ValueError, OverflowError) as error:
            return self.send_json(400, dict(error=str(error)))
        except sqlite3.Error:
            return self.send_json(503, dict(error='DB 조회 실패: 경로·스키마·잠금 상태를 확인하세요.'))
        self.send_json(200, payload)

    def serve_static(self, path):
        if self.web_root is None or path.startswith('/api/'):
            return self.send_json(404, dict(error='Not found'))
        target = (self.web_root/unquote(path).lstrip('/')).resolve()
        if path == '/':
            target = (self.web_root/'index.html').resolve()
        allowed = {'.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.mp4', '.woff', '.woff2', '.ttf'}
        if not target.is_relative_to(self.web_root) or not target.is_file() or target.suffix.lower() not in allowed:
            return self.send_json(404, dict(error='Not found'))
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(target.stat().st_size))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        with target.open('rb') as source:
            shutil.copyfileobj(source, self.wfile, length=65536)

    def send_json(self, status, payload):
        body = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        if self.allow_origin and self.headers.get('Origin') == self.allow_origin:
            self.send_header('Access-Control-Allow-Origin', self.allow_origin)
            self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_json(405, dict(error='Read-only API: GET only'))

    do_DELETE = do_POST
    do_PUT = do_POST
    do_PATCH = do_POST


def make_server(database, host='127.0.0.1', port=8091, web_root=None, allow_origin=None):
    root = Path(web_root).resolve() if web_root else None
    handler = partial(ApiHandler, service=SnapshotService(database), web_root=root, allow_origin=allow_origin)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT/'runtime/edge_events.db')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--web-root', type=Path, help='선택: 새 대시보드 dist 폴더를 같은 서버에서 제공')
    parser.add_argument('--allow-origin', help='개발용 별도 프런트 origin 하나만 허용. 예: http://localhost:5173')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    if args.web_root and not (args.web_root/'index.html').is_file():
        parser.error('--web-root에 index.html이 있어야 합니다.')
    if args.allow_origin:
        origin = urlsplit(args.allow_origin)
        if origin.scheme not in ('http', 'https') or not origin.netloc or origin.path or origin.query or origin.fragment:
            parser.error('--allow-origin에는 경로 없는 http(s) origin을 지정하세요.')
    server = make_server(args.database, args.host, args.port, args.web_root, args.allow_origin)
    print(f'API: http://{args.host}:{args.port}/api/snapshot\nRead-only DB: {args.database.resolve()}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
