"""Persist one shared sensor stream; adapt the supplied read-only temperature API."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

from airguard_control.temperature.dashboard_api import SnapshotService, clean
from airguard_control.temperature.temperature_alert import dashboard_payload

FIELDS = ('timestamp','temperature_c','humidity_pct','pm25_ug_m3','intake_rpm',
          'exhaust_rpm','intake_pwm','exhaust_pwm','pressure_hpa','pm1_ug_m3',
          'pm10_ug_m3','bme_ok','pms_ok')


class IntegrationStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock()
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS sensor_readings ('+
            ','.join(k+' REAL'+(' PRIMARY KEY' if k=='timestamp' else '') for k in FIELDS)+')')
        self.db.execute('CREATE TABLE IF NOT EXISTS control_status (id INTEGER PRIMARY KEY, timestamp REAL, '
            'temperature_c REAL, predicted_temp_c REAL, intake_percent INTEGER, exhaust_percent INTEGER, '
            'recovery_seconds REAL, model_name TEXT, reason TEXT, candidate_json TEXT, '
            'policy_limit_c REAL, command_sent INTEGER, demand_only INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS control_events_v2 (timestamp REAL, temperature_c REAL, '
            'predicted_temp_c REAL, intake_percent INTEGER, exhaust_percent INTEGER, reason TEXT, command_sent INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS integrated_status (id INTEGER PRIMARY KEY, data TEXT)')
        self.db.commit()
        self.last_sample=self.db.execute('SELECT MAX(timestamp) FROM sensor_readings').fetchone()[0] or 0
        self.last_pair=None
        self.service=SnapshotService(self.path)

    def record(self, data, result, decision):
        rows=list(data.get('history') or [])
        if data.get('sensor'):rows.append(data['sensor'])
        rows={r['timestamp']:r for r in rows if r.get('timestamp',0)>self.last_sample}
        with self.lock, self.db:
            for stamp,row in sorted(rows.items()):
                self.db.execute('INSERT OR IGNORE INTO sensor_readings VALUES ('+','.join('?' for _ in FIELDS)+')',
                    tuple(row.get(k) for k in FIELDS))
                self.last_sample=stamp
            if decision:
                sample, chosen, reason=decision
                model=chosen.model_name
                prediction=chosen.predicted_temp_c if ('LSTM' in model or 'linear estimate' in model) else None
                if prediction is not None and not math.isfinite(prediction):prediction=None
                candidates=[clean(asdict(c)) for c in chosen.candidate_comparison]
                self.db.execute('INSERT OR REPLACE INTO control_status VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (sample.timestamp,sample.temperature_c,prediction,chosen.intake_pwm,chosen.exhaust_pwm,
                     chosen.recovery_seconds,model,reason,json.dumps(candidates,allow_nan=False),
                     chosen.policy_limit_c,0,1))  # Temperature demand is not the final command.
                result['temperatureDemand'].update(predictedTemperatureC=prediction,
                    recoverySeconds=chosen.recovery_seconds)
            pair=tuple(result['proposed'][k] for k in ('intake','exhaust'))
            if pair!=self.last_pair or result['commandSent']:
                sensor=data.get('sensor') or {}
                reason='PM: '+result['smokeDemand']['reason']+' / 온도: '+result['temperatureDemand']['reason']
                self.db.execute('INSERT INTO control_events_v2 VALUES (?,?,?,?,?,?,?)',
                    (result['timestamp'],sensor.get('temperature_c'),None,*pair,reason,int(result['commandSent'])))
                self.last_pair=pair
            self.db.execute('INSERT OR REPLACE INTO integrated_status VALUES (1,?)',
                (json.dumps(clean(result),ensure_ascii=False,allow_nan=False),))

    def snapshot(self,seconds=300,date=None,now=None):
        now=time.time() if now is None else now
        with self.lock:
            snapshot=self.service.snapshot(seconds,date,now=now)
            row=self.db.execute('SELECT data FROM integrated_status WHERE id=1').fetchone()
        payload=dashboard_payload(snapshot)
        status=json.loads(row[0]) if row else None
        status_fresh=status is not None and 0<=now-status['timestamp']<=5 and status.get('mode') in ('live','shadow','paused')
        payload['integratedControl']=status if status_fresh else None
        payload['controlStale']=payload['controlStale'] or not status_fresh
        sensor=payload.get('sensor') or {}
        temperature_valid=bool(sensor.get('bme_ok')) and sensor.get('temperature_c') is not None
        age=payload.get('ageSec')
        payload['stale']=age is None or not -2<=age<=5
        payload['serialConnected']=not payload['stale']
        if payload['stale'] or not temperature_valid:
            payload['temperatureAlert']=dict(state='unknown',is_anomaly=False,label='온도 수신 확인 필요',reason='온도 수신 지연 또는 센서 오류')
            payload['abnormal']=False
            payload['current']=None
            for event in payload['incidents']:
                if event['status']=='active':event['status']='unconfirmed'
        if payload['controlStale'] or not temperature_valid:
            payload['control']=None
            payload['predictedTemperatureC']=None
            payload['forecastHorizonSec']=None
        payload['source']='통합 센서·팬 런타임 SQLite · ESP32 USB Serial'
        payload['message']='실측 수신 중 · '+('팬 자동 운전' if status_fresh and status['mode']=='live' else '제어 요구 계산 · 명령 미전송') if not payload['stale'] else '센서 수신 지연'
        return payload

    def close(self):self.db.close()
