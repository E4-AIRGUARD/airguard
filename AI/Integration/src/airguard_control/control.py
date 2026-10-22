"""온도 v6·PM 팬 자동제어 통합 실행기. 기본은 팬을 쓰지 않는 관찰 모드.

사용: python scripts/run_control.py --help 또는 airguard-control --help
"""
import argparse
import hmac
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time
import uuid
from urllib.request import urlopen
from urllib.parse import urlsplit,parse_qs

from airguard_control.paths import RUNTIME_DIR, model_path

from airguard_control.pm.smoke_fan import SmokeFan, combine_max
from airguard_control.temperature.fan_power import combo_power_w, candidate_power_w, POWER_METHOD
from airguard_control.sensor_bridge import Telemetry, parse_data


def finite(value):return isinstance(value,(int,float)) and math.isfinite(value)


def clean(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    return value


def valid_pair(pair, allow_zero=False):
    allowed={35,60,100}|({0} if allow_zero else set())
    if len(pair)!=2 or any(not finite(v) or v not in allowed for v in pair):
        raise ValueError('PWM must be an exact supported stage; no rounding')
    return tuple(int(v) for v in pair)


class ActualEnergy:
    """Integrate actual feedback once. Neither controller's proposed energy is added."""
    def __init__(self):self.last=None;self.wh=0.;self.covered=0.;self.excluded=0.
    def observe(self,row):
        if self.last and row['timestamp']<=self.last['timestamp']:return
        if self.last:
            dt=row['timestamp']-self.last['timestamp']
            power=combo_power_w(self.last.get('intake_rpm'),self.last.get('exhaust_rpm'))
            if 0<dt<=2.5 and power is not None:self.wh+=power*dt/3600;self.covered+=dt
            elif dt>0:self.excluded+=dt
        self.last=dict(row)


class Coordinator:
    def __init__(self,temperature,smoke,sample_factory,send=None):
        self.temperature=temperature;self.smoke=smoke;self.sample_factory=sample_factory
        self.send=send;self.last_sample=None;self.last_sent=None;self.sent_at=None
        self.feedback=None;self.energy=ActualEnergy();self.latest_decision=None
        # Rate adaptation must not attribute another controller's output to smoke.
        original_update=smoke.ctl._update_rate
        def update_rate(now,pm):
            matches=self.feedback==smoke.ctl.cur
            if not matches:self.reset_smoke_rate()
            original_update(now,pm)
            if not matches:self.reset_smoke_rate()
        smoke.ctl._update_rate=update_rate

    def reset_smoke_rate(self):
        ctl=self.smoke.ctl
        ctl.prev=None;ctl.pred_acc=ctl.act_acc=0.;ctl.rate=1.

    def step(self,data,wall=None,clock=None,enabled=True):
        wall=time.time() if wall is None else wall
        clock=time.monotonic() if clock is None else clock
        row=data.get('sensor') or {}
        timestamp=row.get('timestamp')
        fresh=data.get('available') and not data.get('stale') and finite(timestamp) and 0<=wall-timestamp<=5
        new=fresh and (self.last_sample is None or timestamp>self.last_sample)
        feedback=None
        if fresh:
            values=(row.get('intake_pwm'),row.get('exhaust_pwm'))
            if all(finite(v) and 0<=v<=100 for v in values):feedback=tuple(int(v) for v in values)
        self.feedback=','.join(map(str,feedback)) if feedback else None
        if new:
            self.last_sample=timestamp;self.energy.observe(row)
            if row.get('bme_ok',True) and finite(row.get('temperature_c')):
                sample=self.sample_factory(timestamp=timestamp,temperature_c=row['temperature_c'],
                    humidity_pct=row.get('humidity_pct'),pm25_ug_m3=row.get('pm25_ug_m3'),
                    intake_pwm=row.get('intake_pwm'),exhaust_pwm=row.get('exhaust_pwm'),
                    intake_rpm=row.get('intake_rpm'),exhaust_rpm=row.get('exhaust_rpm'))
                self.temperature.observe(sample)
        # In shadow mode the baseline is actual feedback, never the unsent proposal.
        if self.send is None and feedback is not None and self.temperature.current_pair!=feedback:
            self.temperature.current_pair=feedback
            self.temperature.last_pwm_change=clock
        pm=row.get('pm25_ug_m3') if new and row.get('pms_ok',True) else None
        smoke=self.smoke.step(pm,now=clock)
        decision=self.temperature.decide(wall_time=wall,monotonic_time=clock)
        self.latest_decision=decision
        temp_pair=(35,35);temp_reason='온도 유효 표본 없음 · 기본 요구 35/35';prediction=None;model='unavailable'
        if decision:
            _,result,temp_reason=decision
            temp_pair=valid_pair((result.intake_pwm,result.exhaust_pwm))
            prediction=result.predicted_temp_c;model=result.model_name
        pair=valid_pair(combine_max(smoke,*temp_pair))
        if not enabled:pair=(0,0)
        sent=False
        if self.send is not None and (pair!=self.last_sent or self.sent_at is None or clock-self.sent_at>=5):
            # Confirm only AFTER a successful actual write, and use the final pair.
            accepted=self.send(*pair)
            if accepted is False:pair=(0,0);enabled=False
            self.temperature.confirm_applied(*pair,now=clock)
            self.last_sent=pair;self.sent_at=clock;sent=True
        return clean(dict(timestamp=wall,mode=('live' if enabled else 'paused') if self.send else 'shadow',policy='per_fan_max',
            policyConfirmed=bool(self.send),commandSent=sent,sensorFresh=bool(fresh),sensorTimestamp=timestamp,
            smokeDemand=dict(intake=smoke.intake,exhaust=smoke.exhaust,reason=smoke.reason,pm=smoke.pm),
            temperatureDemand=dict(intake=temp_pair[0],exhaust=temp_pair[1],reason=temp_reason,
                                   predictedTemperatureC=prediction,model=model),
            proposed=dict(intake=pair[0],exhaust=pair[1]),lastSent=self.last_sent,feedback=feedback,
            observedPowerW=combo_power_w(row.get('intake_rpm'),row.get('exhaust_rpm')) if fresh else None,
            observedEnergyWh=self.energy.wh if self.energy.covered else None,
            coveredSeconds=self.energy.covered,excludedSeconds=self.energy.excluded,
            proposedPowerW=candidate_power_w(*pair),powerMethod=POWER_METHOD,
            smokeDemandEnergyJ=smoke.energy_j,energyMeasured=False,
            energyNote='Actual RPM integrated once. Smoke demand energy is separate and hypothetical.',
            adaptationActive=self.feedback==self.smoke.ctl.cur))


class SerialOwner:
    """One exclusive port for reading and writing; never run beside sensor_bridge."""
    def __init__(self,port):
        import serial
        self.lock=threading.Lock();self.stop_event=threading.Event();self.state=Telemetry()
        self.connection=serial.Serial(port=None,baudrate=115200,timeout=.3,exclusive=True)
        self.connection.port=port;self.connection.dtr=False;self.connection.rts=False
        self.connection.open();self.state.serial_ok=True
        self.thread=threading.Thread(target=self.read,daemon=True);self.thread.start()
    def read(self):
        try:
            while not self.stop_event.is_set():
                row=parse_data(self.connection.readline(4096).decode('utf-8',errors='replace'))
                if row:
                    with self.state.lock:self.state.rows.append(row)
        except Exception as error:
            with self.state.lock:self.state.serial_ok=False;self.state.error=str(error)
    def send(self,intake,exhaust):
        pair=valid_pair((intake,exhaust),allow_zero=True)
        command='0\n' if pair==(0,0) else f'F {pair[0]} {pair[1]}\n'
        with self.lock:
            if not self.state.serial_ok:raise ConnectionError('Sensor serial disconnected')
            raw=command.encode('ascii')
            if self.connection.write(raw)!=len(raw):raise ConnectionError('Incomplete fan command')
            self.connection.flush()
    def close(self):
        self.stop_event.set();self.thread.join(timeout=1);self.connection.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live',action='store_true')
    ap.add_argument('--approve-max-policy',action='store_true')
    ap.add_argument('--shutdown',choices=['hold','0,0','35,35','100,100'])
    ap.add_argument('--serial-port')
    ap.add_argument('--source-api',default='http://127.0.0.1:8094/api/temperature')
    ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=8095)
    ap.add_argument('--log',type=Path,default=RUNTIME_DIR/'integrated_decisions.jsonl')
    ap.add_argument('--database',type=Path,default=RUNTIME_DIR/'integrated_events.db')
    ap.add_argument('--event-db',type=Path,default=RUNTIME_DIR/'pm_pending.sqlite3',
                    help='Local PM recovery upload queue (no automatic MySQL connection)')
    ap.add_argument('--device-id',default='jetson-1')
    ap.add_argument('--temperature-model',type=Path,default=model_path('temperature/lstm_temperature_trajectory_v6.pt'),help='온도 모델 경로')
    ap.add_argument('--smoke-model',type=Path,default=model_path('pm/model.json'),help='PM 모델 경로')
    ap.add_argument('--temperature-event-db',type=Path,default=RUNTIME_DIR/'temperature_pending.sqlite3')
    ap.add_argument('--recovery-camera-url',help='Existing smoke camera API origin; reuses its frames')
    ap.add_argument('--dashboard-lease-key',type=Path)
    ap.add_argument('--lease-timeout',type=float,default=15)
    args=ap.parse_args()
    if args.live and not(args.approve_max_policy and args.shutdown and args.serial_port):
        ap.error('--live requires --approve-max-policy, --shutdown and --serial-port')
    if args.dashboard_lease_key and (not args.live or args.shutdown!='0,0' or not 5<=args.lease_timeout<=60):
        ap.error('Dashboard leases require live mode, shutdown 0,0, and timeout 5..60')
    from airguard_control.dashboard_lease import DashboardLease,FanLeaseGuard
    lease=DashboardLease(args.lease_timeout) if args.dashboard_lease_key else None
    lease_key=args.dashboard_lease_key.read_text().strip() if lease else None
    if lease and len(lease_key)<32:ap.error('Lease key too short')
    guard=None
    from airguard_control.temperature.sensor_buffer import SensorSample
    from airguard_control.temperature.temperature_controller import TemperatureController
    import torch
    torch.set_num_threads(1)
    from airguard_control.integration_store import IntegrationStore
    from airguard_control.events.pm_recovery import PMRecovery
    temperature=TemperatureController(args.temperature_model)
    smoke=SmokeFan(model_path=str(args.smoke_model.resolve()))
    store=IntegrationStore(args.database)
    recovery=PMRecovery(args.event_db,args.device_id)
    temperature_recovery=PMRecovery(args.temperature_event_db,args.device_id,category='temperature')
    recovery_camera=None
    if args.recovery_camera_url:
        from airguard_control.events.event_photos import EventPhotos
        from airguard_control.events.recovery_camera import RecoveryCamera
        frame=args.event_db.parent/'recovery_camera.jpg'
        recovery.recorder.photos=EventPhotos(frame,args.event_db.parent)
        temperature_recovery.recorder.photos=EventPhotos(frame,args.temperature_event_db.parent)
        recovery_camera=RecoveryCamera(args.recovery_camera_url,frame)
        recovery_camera.start()
    state={'status':None,'sensor_data':dict(available=False,stale=True,sensor=None,history=[],events=[])};lock=threading.Lock();stop=threading.Event();owner=None;serving=False
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if not lease or self.path!='/api/dashboard-lease':self.send_error(404);return
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+lease_key):
                self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=2048:raise ValueError('Body size')
                self.connection.settimeout(3)
                data=json.loads(self.rfile.read(size))
                payload=lease.update(data['client'],data['action'],data['sequence'])
                body=json.dumps(payload).encode()
            except (ValueError,KeyError,TypeError):self.send_error(400);return
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_GET(self):
            url=urlsplit(self.path)
            if url.path.startswith('/api/recovery-photo/'):
                parts=url.path.strip('/').split('/')
                try:
                    if len(parts)!=4 or any(str(uuid.UUID(v))!=v for v in parts[2:]):raise ValueError('Invalid ID')
                    photo=args.event_db.parent/'photos'/parts[2]/(parts[3]+'.jpg')
                    if not photo.is_file():photo=args.temperature_event_db.parent/'photos'/parts[2]/(parts[3]+'.jpg')
                    if not photo.is_file() or photo.stat().st_size>10*1024*1024:raise ValueError('Missing photo')
                    raw=photo.read_bytes()
                    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):raise ValueError('Invalid JPEG')
                except (ValueError,OSError):self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            elif url.path=='/api/control':
                with lock:payload=dict(state['status']) if state['status'] else dict(mode='starting',timestamp=None)
                stamp=payload.get('timestamp');payload['stale']=not finite(stamp) or time.time()-stamp>5
            elif url.path=='/api/temperature':
                try:
                    query=parse_qs(url.query,keep_blank_values=True)
                    if set(query)-{'seconds','date'} or any(len(v)!=1 for v in query.values()):raise ValueError('seconds/date only')
                    payload=store.snapshot(int(query.get('seconds',['300'])[0]),query.get('date',[None])[0])
                except (ValueError,OverflowError):self.send_error(400);return
            elif url.path=='/api/health':
                payload=dict(readOnly=not bool(lease),databaseExists=args.database.is_file(),dashboardLease=bool(lease))
            else:self.send_error(404);return
            body=json.dumps(clean(payload),ensure_ascii=False,allow_nan=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin','http://127.0.0.1:8091');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    args.log.parent.mkdir(parents=True,exist_ok=True)
    try:
        if args.live:
            owner=SerialOwner(args.serial_port)
            # Do not treat the normal first-frame wait as a failed sensor.
            deadline=time.monotonic()+8
            while not owner.state.snapshot()['available'] and time.monotonic()<deadline:
                if stop.wait(.1):break
            if not owner.state.snapshot()['available']:raise RuntimeError('No initial ESP32 DATA frame')
        if lease:
            guard=FanLeaseGuard(lease,owner.send);guard.start()
        coord=Coordinator(temperature,smoke,SensorSample,guard.send if guard else owner.send if owner else None)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        serving=True
        print(f'Integrated control: {"LIVE" if args.live else "SHADOW (no writes)"}, API {args.host}:{args.port}',flush=True)
        with args.log.open('a',encoding='utf-8') as log:
            while not stop.is_set():
                start=time.monotonic()
                try:
                    if owner:data=owner.state.snapshot()
                    else:
                        try:
                            with urlopen(args.source_api,timeout=2) as r:data=json.load(r)
                        except (OSError,ValueError):data=dict(available=False,stale=True)
                    result=coord.step(data,enabled=lease.status()['active'] if lease else True)
                    recovery.observe(data,result)
                    temperature_recovery.observe(data,result)
                    if lease:result['dashboardLease']=lease.status()
                    store.record(data,result,coord.latest_decision)
                    result['sensor']=data.get('sensor')
                    with lock:state['sensor_data']=data
                    result['cycleSeconds']=time.monotonic()-start
                    result['shutdownPolicy']=args.shutdown or 'no writes'
                except Exception as error:
                    result=dict(timestamp=time.time(),mode='fault',error=str(error),commandSent=False)
                    if args.live:stop.set()
                with lock:state['status']=result
                log.write(json.dumps(clean(result),ensure_ascii=False,allow_nan=False)+'\n');log.flush()
                stop.wait(max(0,1-(time.monotonic()-start)))
    finally:
        if owner:
            try:
                if guard:guard.close()
                elif args.shutdown!='hold':owner.send(*map(int,args.shutdown.split(',')))
            finally:owner.close()
        smoke.close()
        recovery.close()
        temperature_recovery.close()
        if recovery_camera:recovery_camera.close()
        if serving:server.shutdown()
        server.server_close()
        store.close()


if __name__=='__main__':main()
