"""Read-only temperature dashboard HTTP API; never opens serial or controls fans."""
import argparse
from datetime import datetime, timezone, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

from fan_power import combo_power_w, observed_energy, POWER_METHOD
from temperature_events import temperature_events

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
MAX_ROWS = 50000


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, bytes):
        return int.from_bytes(value, 'little', signed=True) if len(value) == 8 else None
    return value


class SnapshotService:
    def __init__(self, database):
        self.database = Path(database).resolve()

    def snapshot(self, seconds=300, now=None):
        if not 1 <= seconds <= 86400:
            raise ValueError('seconds must be between 1 and 86400')
        now = time.time() if now is None else now
        result = dict(available=False, stale=True, ageSec=None, sensor=None, control=None,
                      history=[], events=[], recentRecords=[], abnormal=False, state='unknown',
                      predictedTemperatureC=None, estimatedPowerW=None, powerMeasured=False,
                      powerMethod=POWER_METHOD, historySeconds=seconds, historyTruncated=False,
                      message='센서 데이터 없음', source='temperature SQLite')
        if not self.database.is_file():
            return result
        try:
            with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                db.row_factory = sqlite3.Row
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'sensor_readings' not in tables:
                    return result
                latest = db.execute('SELECT * FROM sensor_readings ORDER BY timestamp DESC LIMIT 1').fetchone()
                if latest is None:
                    return result
                rows = [clean(dict(r)) for r in db.execute(
                    'SELECT * FROM sensor_readings WHERE timestamp>=? AND timestamp<=? '
                    'ORDER BY timestamp DESC LIMIT ?', (now-seconds, now, MAX_ROWS+1))]
                result['historyTruncated'] = len(rows) > MAX_ROWS
                rows = list(reversed(rows[:MAX_ROWS]))
                sensor = clean(dict(latest))
                control = None
                if 'control_status' in tables:
                    record = db.execute('SELECT * FROM control_status WHERE id=1').fetchone()
                    if record:
                        control = clean(dict(record))
                        try:
                            control['candidates'] = clean(json.loads(control.get('candidate_json') or '[]'))
                        except (TypeError, ValueError):
                            control['candidates'] = []
                records = []
                if 'control_events_v2' in tables:
                    records = [clean(dict(r)) for r in db.execute(
                        'SELECT * FROM control_events_v2 ORDER BY timestamp DESC LIMIT 20')]
        except sqlite3.Error:
            result['message'] = 'DB 조회 실패 또는 잠금; 재시도 필요'
            return result
        age = now - sensor['timestamp']
        stale = not 0 <= age <= 5
        events = temperature_events(rows)
        active = next((e for e in events if e['status'] == 'active'), None)
        # A stale open incident remains unresolved; it is not presented as fresh telemetry.
        if active and stale:
            active['status'] = 'interrupted'
        abnormal = not stale and active is not None
        control_age = now - control['timestamp'] if control else None
        control_stale = control_age is None or not 0 <= control_age <= 5
        result.update(available=True, stale=stale, ageSec=age, sensor=sensor, control=control,
                      controlStale=control_stale, history=rows, events=events,
                      recentRecords=records, abnormal=abnormal,
                      state='unknown' if stale else ('abnormal' if abnormal else 'normal'),
                      predictedTemperatureC=None if control_stale else control.get('predicted_temp_c'),
                      estimatedPowerW=None if stale else combo_power_w(sensor.get('intake_rpm'), sensor.get('exhaust_rpm')),
                      energy=observed_energy(rows),
                      message='센서 수신 지연' if stale else ('온도 이상 감지' if abnormal else '정상 수신'))
        return clean(result)


def dashboard_payload(snapshot):
    """Keep snapshot fields available for UI binding without changing HTML layout."""
    return snapshot


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, service, allow_origin, **kwargs):
        self.service, self.allow_origin = service, allow_origin
        super().__init__(*args, **kwargs)

    def send_json(self, status, payload):
        data = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', self.allow_origin)
        self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path not in ('/api/snapshot', '/api/temperature', '/api/events'):
            self.send_json(404, {'error': 'Not found'})
            return
        query = parse_qs(url.query)
        if set(query) - {'seconds', 'date'}:
            self.send_json(400, {'error': 'Unsupported query parameter'})
            return
        try:
            seconds = int(query.get('seconds', ['300'])[0])
            date = query.get('date', [None])[0]
            if date:
                date = datetime.strptime(date, '%Y-%m-%d').date()
            snapshot = self.service.snapshot(seconds)
        except (ValueError, OverflowError):
            self.send_json(400, {'error': 'Invalid seconds or date'})
            return
        if url.path == '/api/events':
            events = snapshot['events']
            if date:
                events = [e for e in events if datetime.fromtimestamp(e['timestamp'], KST).date() == date]
            self.send_json(200, dict(events=events, stale=snapshot['stale'],
                                    historySeconds=seconds, historyTruncated=snapshot['historyTruncated']))
        else:
            self.send_json(200, dashboard_payload(snapshot))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.allow_origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def do_POST(self):
        self.send_json(405, {'error': 'Read-only API'})


def make_server(database, host='127.0.0.1', port=8094, allow_origin='http://127.0.0.1:8091'):
    handler = partial(Handler, service=SnapshotService(database), allow_origin=allow_origin)
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT/'runtime/edge_events.db')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8094)
    parser.add_argument('--allow-origin', default='http://127.0.0.1:8091')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    server = make_server(args.database, args.host, args.port, args.allow_origin)
    print(f'Temperature API: http://{args.host}:{args.port}/api/temperature')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
