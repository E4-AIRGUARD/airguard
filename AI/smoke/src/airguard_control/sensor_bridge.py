"""ESP32 telemetry only: reads DATA frames, never sends fan/serial commands."""
import argparse
from airguard_control.paths import RUNTIME_DIR
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit


def parse_data(line, now=None):
    fields = line.strip().split(',')
    if len(fields) != 14 or fields[0] != 'DATA':
        return None
    try:
        values = [float(v) for v in fields[1:]]
        if not all(math.isfinite(v) for v in values):
            # BME error may deliberately report nan; keep independent PM and RPM.
            if not all(math.isfinite(values[i]) for i in [0,1,2,3,4,8,9,10,11,12]):
                return None
        ms, ip, ir, ep, er, temp, rh, pressure, pm1, pm25, pm10, bme, pms = values
        if bme not in (0,1) or pms not in (0,1) or ms < 0:
            return None
        if not (0 <= ip <= 100 and 0 <= ep <= 100 and ir >= 0 and er >= 0):
            return None
        bme_ok = not bme and all(math.isfinite(v) for v in [temp,rh,pressure]) and -40 <= temp <= 85 and 0 <= rh <= 100 and 300 <= pressure <= 1100
        pms_ok = not pms and all(0 <= v <= 65535 for v in [pm1,pm25,pm10])
        return dict(timestamp=time.time() if now is None else now, uptime_ms=ms,
            temperature_c=temp if bme_ok else None, humidity_pct=rh if bme_ok else None,
            pressure_hpa=pressure if bme_ok else None, pm1_ug_m3=pm1 if pms_ok else None,
            pm25_ug_m3=pm25 if pms_ok else None, pm10_ug_m3=pm10 if pms_ok else None,
            intake_pwm=ip, exhaust_pwm=ep, intake_rpm=ir, exhaust_rpm=er,
            bme_ok=bme_ok, pms_ok=pms_ok)
    except (ValueError, OverflowError):
        return None


def power_w(row):
    return sum(5*.27*(row[key]/7500)**3 for key in ['intake_rpm','exhaust_rpm'])


class Telemetry:
    def __init__(self):
        self.lock=threading.Lock()
        self.rows=deque(maxlen=7200)
        self.serial_ok=False
        self.error='센서 수신 시작 대기'

    def snapshot(self, seconds=300, now=None):
        now=time.time() if now is None else now
        with self.lock:
            last=dict(self.rows[-1]) if self.rows else None
            rows=[dict(r) for r in self.rows if r['timestamp'] >= now-seconds]
            connected=self.serial_ok
            error=self.error
        age=now-last['timestamp'] if last else None
        stale=not connected or age is None or not 0 <= age <= 5
        energy=covered=excluded=0
        for left,right in zip(rows,rows[1:]):
            gap=right['timestamp']-left['timestamp']
            if 0 < gap <= 2.5:
                energy+=power_w(left)*gap/3600
                covered+=gap
            elif gap>0:excluded+=gap
        stride=max(1,math.ceil(len(rows)/600))
        history=rows[::stride]
        if rows and (not history or history[-1]['timestamp']!=rows[-1]['timestamp']):history.append(rows[-1])
        return dict(available=last is not None, stale=stale, ageSec=round(age,2) if age is not None else None,
            serialConnected=connected, message=error if not connected else ('센서 수신 지연' if stale else 'ESP32 실측 수신 중'),
            sensor=last, control=None, history=history, events=[], source='ESP32 USB Serial · DATA',
            estimatedPowerW=power_w(last) if last else None, powerMeasured=False,
            energy=dict(energy_wh=energy if covered else None,covered_seconds=covered,excluded_seconds=excluded),
            powerMethod='rated_power_times_rpm_ratio_cubed_v1', historySeconds=seconds)


def read_serial(state, port, database):
    import serial
    database.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(database)
    db.execute('CREATE TABLE IF NOT EXISTS telemetry (timestamp REAL PRIMARY KEY, data TEXT NOT NULL)')
    while True:
        connection=None
        try:
            connection=serial.Serial(port=None,baudrate=115200,timeout=.5,exclusive=True)
            connection.port=port
            connection.dtr=False
            connection.rts=False
            connection.open()
            with state.lock:state.serial_ok=True;state.error=''
            print('Telemetry serial opened:',port,flush=True)
            while True:
                raw=connection.readline(4096).decode('utf-8',errors='replace')
                row=parse_data(raw)
                if row:
                    with state.lock:state.rows.append(row)
                    with db:db.execute('INSERT OR REPLACE INTO telemetry VALUES (?,?)',(row['timestamp'],json.dumps(row,allow_nan=False)))
        except (serial.SerialException,OSError) as error:
            with state.lock:state.serial_ok=False;state.error='ESP32 연결 끊김 · 재연결 중'
            print(type(error).__name__,str(error),flush=True)
        finally:
            if connection:connection.close()
        time.sleep(2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8094)
    parser.add_argument('--serial-port',required=True)
    parser.add_argument('--database',type=Path,default=RUNTIME_DIR/'telemetry.db')
    args=parser.parse_args()
    state=Telemetry()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            url=urlsplit(self.path)
            if url.path!='/api/temperature':self.send_error(404);return
            try:seconds=max(60,min(3600,int(parse_qs(url.query).get('seconds',['300'])[0])))
            except ValueError:seconds=300
            body=json.dumps(state.snapshot(seconds),ensure_ascii=False,allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    threading.Thread(target=read_serial,args=(state,args.serial_port,args.database),daemon=True).start()
    print(f'Telemetry API http://{args.host}:{args.port}/api/temperature (no serial writes)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
