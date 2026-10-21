"""Serve the dashboard; relay authenticated per-tab fan leases to Jetson."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import importlib.util
import json
import math
import sqlite3
import socket
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent


class DashboardServer(ThreadingHTTPServer):
    # Windows otherwise permits two reused listeners; an old server may answer.
    allow_reuse_address=False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    return value


def read_temperature(database, power, now=None):
    now = time.time() if now is None else now
    base = dict(available=False, stale=True, ageSec=None, sensor=None, control=None,
                history=[], events=[], source='handoff_v6', powerMeasured=False)
    if not database.is_file():
        return dict(base, message='온도 DB 연결 대기 · 아직 기록 파일이 없습니다.')
    try:
        # mode=ro never creates or changes a controller database.
        connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN')
            sensor = connection.execute('SELECT * FROM sensor_readings ORDER BY timestamp DESC LIMIT 1').fetchone()
            if sensor is None:
                return dict(base, message='온도 DB 연결됨 · 센서 기록 대기')
            sensor = dict(sensor)
            timestamp = sensor['timestamp']
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                raise ValueError('Invalid sensor timestamp')
            history = [dict(row) for row in connection.execute(
                'SELECT * FROM sensor_readings WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 600',
                (timestamp - 300,))][::-1]
            control = connection.execute('SELECT * FROM control_status WHERE id=1').fetchone()
            control = dict(control) if control else None
            if control:
                try:
                    control['candidates'] = json.loads(control.pop('candidate_json') or '[]')
                except (ValueError, KeyError, TypeError):
                    control['candidates'] = []
                control_time = control.get('timestamp')
                control['stale'] = (not isinstance(control_time, (float, int)) or
                    not math.isfinite(control_time) or not -5 <= now - control_time <= 5)
            events = [dict(row) for row in connection.execute(
                'SELECT * FROM control_events_v2 ORDER BY timestamp DESC LIMIT 20')]
        finally:
            connection.close()
        age = now - timestamp
        stale = not -5 <= age <= 5
        return clean_json(dict(base, available=True, stale=stale, ageSec=round(age, 1),
            message='센서 갱신 지연 · 마지막 기록' if stale else '센서 수신 중',
            sensor=sensor, control=control, history=history, events=events,
            estimatedPowerW=power.combo_power_w(sensor.get('intake_rpm'), sensor.get('exhaust_rpm')),
            energy=power.observed_energy(history), powerMethod=power.POWER_METHOD))
    except (sqlite3.Error, ValueError, TypeError, OverflowError):
        return dict(base, message='온도 DB를 읽을 수 없습니다. v6 DB 형식과 파일 접근 상태를 확인하세요.')


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, database, power, lease_key=None,
                 control_url='http://100.106.99.75:8095', **kwargs):
        self.database, self.power = database, power
        self.lease_key=lease_key
        self.control_url=control_url.rstrip('/')
        super().__init__(*args, **kwargs)

    def do_POST(self):
        if self.path!='/api/dashboard-lease':self.send_error(404);return
        port=self.server.server_address[1]
        allowed={f'http://127.0.0.1:{port}',f'http://localhost:{port}'}
        origin=self.headers.get('Origin','')
        if origin not in allowed or self.headers.get('Host')!=urlsplit(origin).netloc:
            self.send_error(403);return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.send_error(415);return
        if not self.lease_key:self.send_error(503,'Dashboard fan lease is not configured');return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=2048:raise ValueError('Body size')
            self.connection.settimeout(3)
            payload=json.loads(self.rfile.read(size))
            if set(payload)!={'client','action','sequence'}:raise ValueError('Fields')
            request=Request(self.control_url+'/api/dashboard-lease',
                data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.lease_key},method='POST')
            with urlopen(request,timeout=3) as response:body=response.read(4096)
        except (ValueError,TypeError):self.send_error(400);return
        except (URLError,OSError):self.send_error(503,'Jetson control unavailable');return
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/temperature':
            body = json.dumps(read_temperature(self.database, self.power), ensure_ascii=False,
                              allow_nan=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith('/api/'):
            self.send_error(404)
        else:
            super().do_GET()

def main():
    parser = argparse.ArgumentParser(description="Airguard dashboard local preview")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument('--control-url', default='http://100.106.99.75:8095',
                        help='Trusted Jetson control server origin (configured by the operator)')
    parser.add_argument('--lease-key', type=Path, default=ROOT/'runtime/dashboard_lease.key')
    parser.add_argument("--database", type=Path,
                        default=ROOT / 'integrations/handoff_v6/runtime/edge_events.db')
    parser.add_argument("--temperature-src", type=Path,
                        default=ROOT / 'integrations/handoff_v6/src')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    control = urlsplit(args.control_url)
    if (control.scheme not in ('http','https') or not control.hostname or
            control.username or control.password or control.path not in ('','/') or
            control.query or control.fragment):
        parser.error('--control-url must be an HTTP(S) origin without credentials, path or query')
    power_file = args.temperature_src.resolve() / 'fan_power.py'
    if not power_file.is_file():
        parser.error(f"Missing temperature power module: {power_file}")
    spec = importlib.util.spec_from_file_location('dashboard_fan_power', power_file)
    power = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(power)
    lease_file=args.lease_key
    lease_key=lease_file.read_text().strip() if lease_file.is_file() else None
    handler = partial(DashboardHandler, directory=str(ROOT / 'dist'),lease_key=lease_key,
                      control_url=args.control_url,
                      database=args.database.resolve(), power=power)
    try:
        with DashboardServer((args.host, args.port), handler) as server:
            print(f"Airguard: http://{args.host}:{args.port}/#live (Ctrl+C to stop)", flush=True)
            print(f"Temperature DB (read-only): {args.database.resolve()}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except OSError as error:
        raise SystemExit(f"Cannot start server: {error}. Try --port 8093")

if __name__ == "__main__":
    main()
