"""Team dashboard contract: read main_runner SQLite, never open USB or command fans."""
import argparse
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from dashboard_api import KST, ROOT, SnapshotService, clean
from fan_power import observed_energy


def iso(stamp):
    return datetime.fromtimestamp(stamp, KST).isoformat(timespec='seconds') if stamp is not None else None


def dashboard_payload(snapshot):
    """Preserve telemetry bridge fields, adding smoke-alert-style current/incidents."""
    connected = snapshot['connected']
    control_age = snapshot.get('control_age_seconds')
    control_fresh = control_age is not None and -2 <= control_age <= 10
    control = snapshot.get('control') if control_fresh else None
    alert = snapshot.get('temperature_alert') or dict(
        state='unknown', is_anomaly=False, label='수신 확인 필요', reason='센서 데이터 없음')
    incidents = []
    for event in reversed(snapshot['episodes']):
        incidents.append(dict(event, start=iso(event['start']), end=iso(event['end']),
            durationSec=event['duration_seconds'], peak=event['peak_c']))
    current = next((e for e in incidents if e['status'] == 'active'), None) if connected else None
    sensor = snapshot.get('sensor')
    prediction = control.get('predicted_temp_c') if control else None
    return dict(available=sensor is not None, stale=not connected,
        ageSec=snapshot.get('sensor_age_seconds'), serialConnected=connected,
        message=snapshot.get('message'), sensor=sensor, control=control,
        controlStale=not control_fresh, history=snapshot['readings'],
        events=incidents, incidents=incidents, current=current,
        temperatureAlert=alert, abnormal=bool(connected and alert['is_anomaly']),
        predictedTemperatureC=prediction, forecastHorizonSec=5 if prediction is not None else None,
        thresholdC=26, updated=iso(sensor['timestamp']) if sensor else None,
        source='main_runner SQLite · ESP32 USB Serial',
        estimatedPowerW=sensor.get('estimated_power_w') if sensor else None,
        powerMeasured=False, powerMethod=snapshot['power_estimation_method'],
        energy=observed_energy(snapshot['readings']), date=snapshot['date'],
        controlEvents=snapshot['controls'], truncated=snapshot['truncated'])


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, service, origin, **kwargs):
        self.service, self.origin = service, origin
        super().__init__(*args, **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if self.headers.get('Origin') == self.origin:
            self.send_header('Access-Control-Allow-Origin', self.origin)
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == '/api/health':
            return self.send_json(200, dict(readOnly=True, databaseExists=self.service.database.is_file()))
        if request.path != '/api/temperature':
            return self.send_json(404, dict(error='Not found'))
        try:
            query = parse_qs(request.query, keep_blank_values=True)
            if set(query)-{'seconds', 'date'} or any(len(v) != 1 for v in query.values()):
                raise ValueError('Supported query: seconds, date')
            snapshot = self.service.snapshot(int(query.get('seconds', ['300'])[0]), query.get('date', [None])[0])
            self.send_json(200, dashboard_payload(snapshot))
        except (ValueError, OverflowError) as error:
            self.send_json(400, dict(error=str(error)))
        except sqlite3.Error:
            self.send_json(503, dict(error='SQLite 조회 실패: main과 동일한 DB 경로인지 확인하세요.'))

    def do_OPTIONS(self):
        self.send_json(200, {})

    def do_POST(self):
        self.send_json(405, dict(error='Read-only API'))

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT/'runtime/edge_events.db')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8094)
    parser.add_argument('--allow-origin', default='http://127.0.0.1:8091')
    args = parser.parse_args()
    handler = partial(Handler, service=SnapshotService(args.database), origin=args.allow_origin)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True
    print(f'Temperature API: http://{args.host}:{args.port}/api/temperature')
    print(f'Read-only DB: {args.database.resolve()}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
