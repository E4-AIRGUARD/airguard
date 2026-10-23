"""Aiven compatibility and HTTP checks using a read-only local fixture database."""
import csv
from datetime import datetime
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('recovery_module', ROOT/'recovery_api.py')
api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
from run import DashboardHandler

EVENT = '10000000-0000-0000-0000-000000000001'
PHOTO = '20000000-0000-0000-0000-000000000001'


class Cursor:
    def __init__(self, conn): self.conn=conn; self.cur=conn.cursor(); self.special=None
    def __enter__(self): return self
    def __exit__(self,*args): self.cur.close()
    def execute(self, sql, args=()):
        self.special=None
        if sql.startswith('SHOW COLUMNS FROM '):
            self.special=[{'Field':r[1]} for r in self.conn.execute('PRAGMA table_info('+sql.split()[-1]+')')]
        elif 'information_schema.TABLES' in sql:
            self.special=[{'TABLE_NAME':'e4_recovery_photos'}] if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='e4_recovery_photos'").fetchone() else []
        else: self.cur.execute(sql.replace('%s','?'),args)
    def fetchall(self):
        if self.special is not None:
            result=self.special;self.special=[];return result
        return [dict(r) for r in self.cur.fetchall()]
    def fetchone(self):
        if self.special is not None: return self.special.pop(0) if self.special else None
        row=self.cur.fetchone();return dict(row) if row else None
    def fetchmany(self,n): return [dict(r) for r in self.cur.fetchmany(n)]


class Connection:
    def __init__(self,path):
        self.db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        self.db.row_factory=sqlite3.Row
    def cursor(self): return Cursor(self.db)
    def rollback(self): self.db.rollback()
    def close(self): self.db.close()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/'fixture.db'
        conn=sqlite3.connect(self.db)
        conn.execute('CREATE TABLE e4_recovery_events ('+','.join(k+' TEXT' for k in api.EVENT_FIELDS)+')')
        conn.execute('CREATE TABLE e4_recovery_samples (event_id TEXT,'+','.join(k+' TEXT' for k in api.SAMPLE_FIELDS if k not in ('heat_load','intake_rpm','exhaust_rpm'))+')')
        conn.execute('CREATE TABLE e4_recovery_photos (photo_id TEXT,event_id TEXT,captured_at TEXT,stage TEXT,relative_path TEXT)')
        conn.execute('INSERT INTO e4_recovery_events VALUES (?,?,?,?,?,?,?,?,?)',
            (EVENT,'=formula','2026-10-17 15:01:00',None,'2026-10-17 15:01:05','2026-10-17 15:01:05','completed',5,
             json.dumps({'thresholds':{'pm25':50,'temperature':26},'hold_seconds':3,'password':'must-not-leak'})))
        columns=['event_id']+[k for k in api.SAMPLE_FIELDS if k not in ('heat_load','intake_rpm','exhaust_rpm')]
        for n in range(3):
            data=dict(event_id=EVENT,sample_id=str(n),measured_at=f'2026-10-17 15:01:0{n}',temperature=30,
                      humidity=40,pm25=100,intake_pwm=60,exhaust_pwm=100,valid_json='{"temperature":false,"pm25":true}',phase='active')
            conn.execute('INSERT INTO e4_recovery_samples VALUES ('+','.join('?' for _ in columns)+')',tuple(data.get(k) for k in columns))
        relative='photos/'+EVENT+'/'+PHOTO+'.jpg'
        file=self.root/relative;file.parent.mkdir(parents=True);file.write_bytes(b'\xff\xd8test\xff\xd9')
        conn.execute('INSERT INTO e4_recovery_photos VALUES (?,?,?,?,?)',(PHOTO,EVENT,'2026-10-17 15:01:00','start',relative))
        conn.commit();conn.close()
        self.repo=api.RecoveryRepository(lambda:Connection(self.db),self.root)
    def tearDown(self): self.temp.cleanup()

    def test_legacy_schema_latest_date_and_kst_boundary(self):
        result=self.repo.list_events()
        self.assertEqual(result['date'],'2026-10-18');self.assertEqual(result['total'],1)
        self.assertEqual(result['events'][0]['category'],'combined')
        self.assertEqual(result['events'][0]['started_at'],'2026-10-17T15:01:00Z')
        self.assertNotIn('must-not-leak',json.dumps(result))
        self.assertEqual(self.repo.list_events('2026-10-17')['total'],0)
        self.assertEqual(self.repo.list_events(category='pm25')['total'],0)
        self.assertEqual(self.repo.list_events(status='active')['total'],0)

    def test_optional_event_type_and_pagination(self):
        conn=sqlite3.connect(self.db);conn.execute('ALTER TABLE e4_recovery_events ADD COLUMN event_type TEXT')
        conn.execute("UPDATE e4_recovery_events SET event_type='temperature'")
        for n in range(2,14):
            conn.execute('INSERT INTO e4_recovery_events SELECT ?,device_id,started_at,first_reached_at,recovered_at,recording_ended_at,status,recovery_seconds,config_json,event_type FROM e4_recovery_events WHERE event_id=?',
                         (f'10000000-0000-0000-0000-{n:012d}',EVENT))
        conn.commit();conn.close()
        result=self.repo.list_events(category='temperature',page=2)
        self.assertEqual((result['total'],result['pages'],len(result['events'])),(13,2,3))
        self.assertEqual(result['events'][0]['categoryBasis'],'recorded')
        self.assertEqual(self.repo.list_events(page=99)['page'],2)

    def test_detail_missing_optional_columns_and_invalid_sensor(self):
        detail=self.repo.detail(EVENT)
        self.assertEqual(len(detail['samples']),3)
        self.assertIsNone(detail['samples'][0]['temperature'])
        self.assertIsNone(detail['samples'][0]['intake_rpm'])
        self.assertTrue(detail['photos'][0]['available'])
        self.assertNotIn('relative_path',detail['photos'][0])

    def test_photo_missing_path_escape_and_invalid_jpeg(self):
        self.assertEqual(self.repo.photo(PHOTO),b'\xff\xd8test\xff\xd9')
        self.assertIsNone(self.repo.local_photo(dict(event_id=EVENT,relative_path='../private.jpg')))
        self.assertIsNone(self.repo.local_photo(dict(event_id=EVENT,relative_path='photos/another/a.jpg')))
        path=self.root/'photos'/EVENT/(PHOTO+'.jpg');path.write_bytes(b'invalid')
        with self.assertRaises(LookupError): self.repo.photo(PHOTO)
        path.unlink()
        self.assertFalse(self.repo.detail(EVENT)['photos'][0]['available'])

    def test_no_photo_table_is_supported(self):
        conn=sqlite3.connect(self.db);conn.execute('DROP TABLE e4_recovery_photos');conn.close()
        self.assertEqual(self.repo.detail(EVENT)['photos'],[])
        self.assertEqual(self.repo.detail(EVENT)['photoCount'],0)
        with self.assertRaises(LookupError): self.repo.export_photos(EVENT)

    def test_photo_preview_six_but_archive_has_all_originals(self):
        conn=sqlite3.connect(self.db)
        expected={PHOTO:b'\xff\xd8test\xff\xd9'}
        for n in range(2,206):
            photo_id=f'20000000-0000-0000-0000-{n:012d}'
            relative=f'photos/{EVENT}/{photo_id}.jpg'
            raw=b'\xff\xd8'+str(n).encode()+b'\xff\xd9'
            (self.root/relative).write_bytes(raw);expected[photo_id]=raw
            conn.execute('INSERT INTO e4_recovery_photos VALUES (?,?,?,?,?)',
                         (photo_id,EVENT,'2026-10-17 15:01:01','progress',relative))
        conn.commit();conn.close()
        detail=self.repo.detail(EVENT)
        self.assertEqual(detail['photoCount'],205)
        self.assertEqual(len(detail['photos']),6)
        with self.repo.export_photos(EVENT) as archive, zipfile.ZipFile(archive) as bundle:
            self.assertEqual(len(bundle.namelist()),205)
            for name in bundle.namelist():
                self.assertNotIn('/',name)
                photo_id=name.split('_',1)[1][:-4]
                self.assertEqual(bundle.read(name),expected[photo_id])

    def test_photo_archive_missing_original_does_not_return_partial_zip(self):
        (self.root/'photos'/EVENT/(PHOTO+'.jpg')).unlink()
        with self.assertRaises(LookupError): self.repo.export_photos(EVENT)
        with self.assertRaises(ValueError): self.repo.export_photos('../event')
        with self.assertRaises(LookupError): self.repo.export_photos('10000000-0000-0000-0000-000000000099')

    def test_remote_photo_proxy_uses_configured_origin_and_validates_jpeg(self):
        (self.root/'photos'/EVENT/(PHOTO+'.jpg')).unlink()
        self.repo.photo_api='http://trusted-device:8095'
        with patch.object(api,'urlopen',return_value=io.BytesIO(b'\xff\xd8test\xff\xd9')) as get:
            self.assertEqual(self.repo.photo(PHOTO),b'\xff\xd8test\xff\xd9')
            self.assertEqual(get.call_args.args[0],'http://trusted-device:8095/api/recovery-photo/'+EVENT+'/'+PHOTO)
        with patch.object(api,'urlopen',return_value=io.BytesIO(b'not an image')):
            with self.assertRaises(LookupError):self.repo.photo(PHOTO)
        with patch.object(api,'urlopen',return_value=io.BytesIO(b'\xff\xd8remote\xff\xd9')):
            with self.repo.export_photos(EVENT) as archive, zipfile.ZipFile(archive) as bundle:
                self.assertEqual(bundle.read(bundle.namelist()[0]),b'\xff\xd8remote\xff\xd9')

    def test_csv_all_samples_kst_and_formula_escape(self):
        conn=sqlite3.connect(self.db)
        conn.execute("UPDATE e4_recovery_samples SET phase='=formula' WHERE sample_id='0'")
        for n in range(3,503):
            conn.execute("INSERT INTO e4_recovery_samples SELECT event_id,?,measured_at,temperature,humidity,gas,gas_unit,pm25,intake_pwm,exhaust_pwm,valid_json,phase FROM e4_recovery_samples WHERE sample_id='1'",(str(n),))
        conn.commit();conn.close()
        rows=list(csv.DictReader(io.StringIO(self.repo.export_csv(EVENT).decode('utf-8-sig'))))
        self.assertEqual(len(rows),503)  # Export all rows, including beyond the 500-row fetch batch.
        self.assertEqual(list(rows[0]),['시각 (KST)','PM2.5','온도','습도','흡기/배기 PWM','흡기/배기 RPM','구간'])
        self.assertEqual(rows[0]['구간'],"'=formula")
        self.assertEqual(rows[0]['시각 (KST)'],'2026-10-18T00:01:00+09:00')
        self.assertEqual(rows[0]['PM2.5'],'100.0')
        self.assertEqual(rows[0]['온도'],'')
        self.assertEqual(rows[0]['습도'],'40.0')
        self.assertEqual(rows[0]['흡기/배기 PWM'],'60 / 100')
        self.assertEqual(rows[0]['흡기/배기 RPM'],'— / —')

    def test_csv_fan_order_zero_and_partial_missing_feedback(self):
        conn=sqlite3.connect(self.db)
        conn.execute('ALTER TABLE e4_recovery_samples ADD COLUMN intake_rpm REAL')
        conn.execute('ALTER TABLE e4_recovery_samples ADD COLUMN exhaust_rpm REAL')
        conn.execute("UPDATE e4_recovery_samples SET intake_rpm=0,exhaust_rpm=2700 WHERE sample_id='0'")
        conn.execute("UPDATE e4_recovery_samples SET intake_rpm=2600,exhaust_rpm=2750,valid_json=? WHERE sample_id='1'",
                     (json.dumps({'exhaust_rpm':False,'intake_pwm':False}),))
        conn.commit();conn.close()
        rows=list(csv.DictReader(io.StringIO(self.repo.export_csv(EVENT).decode('utf-8-sig'))))
        self.assertEqual(rows[0]['흡기/배기 RPM'],'0 / 2700')
        self.assertEqual(rows[1]['흡기/배기 RPM'],'2600 / —')
        self.assertEqual(rows[1]['흡기/배기 PWM'],'— / 100')

    def test_csv_empty_event_has_header_without_synthetic_sample(self):
        conn=sqlite3.connect(self.db);conn.execute('DELETE FROM e4_recovery_samples');conn.commit();conn.close()
        rows=list(csv.reader(io.StringIO(self.repo.export_csv(EVENT).decode('utf-8-sig'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]),7)

    def test_invalid_filters_and_injection_ids(self):
        for args in [dict(date_value='2026-02-30'),dict(category="pm25' OR 1=1"),dict(status='bogus'),dict(page=0)]:
            with self.assertRaises(ValueError): self.repo.list_events(**args)
        with self.assertRaises(ValueError): self.repo.detail("' OR 1=1")
        with self.assertRaises(LookupError): self.repo.detail('10000000-0000-0000-0000-000000000099')

    def test_http_json_csv_photos_and_secret_error_redaction(self):
        handler=partial(DashboardHandler,directory=str(ROOT/'dist'),database=self.db,power=None,recovery=self.repo)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(origin+'/api/recovery/events') as r: self.assertEqual(json.load(r)['total'],1)
            with urlopen(origin+'/api/recovery/events/'+EVENT+'/csv') as r:
                self.assertIn('attachment',r.headers['Content-Disposition']);self.assertTrue(r.read().startswith(b'\xef\xbb\xbf'))
            with urlopen(origin+'/api/recovery/events/'+EVENT+'/photos.zip') as r:
                self.assertEqual(r.headers['Content-Type'],'application/zip')
                self.assertIn('_photos.zip',r.headers['Content-Disposition'])
                raw=r.read();self.assertEqual(len(raw),int(r.headers['Content-Length']))
                with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                    self.assertEqual(bundle.read(bundle.namelist()[0]),b'\xff\xd8test\xff\xd9')
            with urlopen(origin+'/api/recovery/photos/'+PHOTO) as r: self.assertEqual(r.headers['Content-Type'],'image/jpeg')
            for path,code in [('/api/recovery/events?page=-1',400),('/api/recovery/events?date=x&date=y',400),('/api/recovery/unknown',404)]:
                with self.assertRaises(HTTPError) as error:urlopen(origin+path)
                self.assertEqual(error.exception.code,code)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(origin+'/api/recovery/events',headers={'Host':'untrusted.invalid'}))
            self.assertEqual(error.exception.code,403)
            def fail():raise RuntimeError('password=secret-host')
            self.repo.connect_fn=fail
            with self.assertRaises(HTTPError) as error:urlopen(origin+'/api/recovery/events')
            self.assertEqual(error.exception.code,503)
            self.assertNotIn('secret-host',error.exception.read().decode())
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
