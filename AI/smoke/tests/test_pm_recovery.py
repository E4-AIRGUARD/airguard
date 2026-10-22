"""Recovery queue integration and upload failure tests; no hardware/network writes."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrated_control import Coordinator, SmokeFan
from pm_recovery import PMRecovery
from event_recorder import EventRecorder, QueueSink
import uploader

BASE = 1792580000.


def sample(t, pm=100, **overrides):
    row = dict(timestamp=BASE+t, pm25_ug_m3=pm, pms_ok=True, bme_ok=True,
               temperature_c=27., humidity_pct=45., intake_pwm=60, exhaust_pwm=100,
               intake_rpm=4200., exhaust_rpm=7000.)
    row.update(overrides)
    return dict(available=True, stale=False, sensor=row)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'nested/pending.sqlite3'
        self.recovery = PMRecovery(self.path, 'test-device')

    def tearDown(self):
        self.recovery.close()
        self.temp.cleanup()

    def rows(self, kind):
        return [json.loads(r[0]) for r in self.recovery.sink.db.execute(
            'SELECT payload FROM queue WHERE kind=? ORDER BY id', (kind,))]

    def observe(self, t, pm=100, fresh=True, **overrides):
        self.recovery.observe(sample(t, pm, **overrides), {'sensorFresh': fresh})

    def test_completion_median_threshold_and_telemetry(self):
        for t, pm in enumerate([100, 100, 50, 50, 50, 50, 50]):
            self.observe(t, pm)
        event = self.rows('event')[-1]
        self.assertEqual(event['status'], 'completed')
        self.assertEqual(event['recovery_seconds'], 6.)
        self.assertEqual(event['category'], 'pm25')
        config = json.loads(event['config_json'])
        self.assertEqual(config['source'], 'integrated_control')
        self.assertEqual(config['pwm_source'], 'device_feedback')
        row = self.rows('sample')[-1]
        self.assertEqual((row['intake_pwm'], row['exhaust_pwm']), (60., 100.))
        self.assertEqual((row['temperature'], row['humidity']), (27., 45.))
        self.assertEqual((row['intake_rpm'], row['exhaust_rpm']), (4200., 7000.))
        self.assertEqual(row['phase'], 'post')
        self.assertTrue(row['measured_at'].startswith('2026-'))
        self.assertIsNone(self.recovery.sink.get_state())

    def test_missing_stale_duplicate_and_bad_sensor_do_not_recover(self):
        self.observe(0)
        self.observe(1, 40)
        self.observe(2, 40)
        self.observe(3, 40)
        count = len(self.rows('sample'))
        self.observe(3, 40)
        self.assertEqual(len(self.rows('sample')), count)
        self.observe(4, 40, pms_ok=False)
        self.observe(5, 40)
        self.observe(6, 40, fresh=False)
        self.observe(7, 40)
        self.assertIsNotNone(self.recovery.recorder.ev)
        for t in (8, 9, 10):
            self.observe(t, 40)
        self.assertEqual(self.rows('event')[-1]['status'], 'completed')

    def test_sampling_gap_restarts_hold_even_without_invalid_call(self):
        for t, pm in [(0, 100), (1, 40), (2, 40), (3, 40), (20, 40)]:
            self.observe(t, pm)
        self.assertIsNotNone(self.recovery.recorder.ev)
        self.assertEqual(self.recovery.recorder.below_since, BASE+20)
        for t in (21, 22, 23):
            self.observe(t, 40)
        self.assertEqual(self.rows('event')[-1]['recovery_seconds'], 23.)

    def test_invalid_values_and_missing_feedback(self):
        for t, pm in enumerate([None, float('nan'), float('inf'), -1]):
            self.observe(t, pm)
        self.assertEqual(self.rows('event'), [])
        self.observe(4, intake_pwm=None)
        self.assertEqual(self.rows('event'), [])
        self.observe(5, bme_ok=False, intake_rpm=float('nan'))
        row = self.rows('sample')[-1]
        self.assertIsNone(row['temperature'])
        self.assertIsNone(row['humidity'])
        self.assertIsNone(row['intake_rpm'])

    def test_restart_interrupts_previous_event_at_last_sample(self):
        self.observe(0)
        self.observe(1)
        self.recovery.sink.close()  # Simulated crash: no recorder.close().
        self.recovery = PMRecovery(self.path, 'test-device')
        event = self.rows('event')[-1]
        self.assertEqual(event['status'], 'interrupted')
        self.assertEqual(event['recording_ended_at'], self.rows('sample')[-1]['measured_at'])
        self.assertIsNone(event['recovered_at'])

    def test_close_interrupts_and_categories_survive(self):
        self.observe(0)
        self.recovery.recorder.close(BASE+1)
        self.assertEqual(self.rows('event')[-1]['status'], 'interrupted')
        rec = EventRecorder(self.recovery.sink, 'test', category='temperature')
        rec.update(BASE+2, 100, 35, 35)
        self.assertEqual(self.rows('event')[-1]['category'], 'temperature')
        rec.close(BASE+3)

    def test_coordinator_pause_records_actual_zero_not_smoke_demand(self):
        class Temperature:
            current_pair = (0, 0)
            def observe(self, value): pass
            def decide(self, **kwargs): return None
            def confirm_applied(self, *args, **kwargs): pass
        calls = []
        smoke = SmokeFan()
        coord = Coordinator(Temperature(), smoke, SimpleNamespace, lambda *p: calls.append(p))
        data = sample(0, intake_pwm=0, exhaust_pwm=0)
        result = coord.step(data, wall=BASE, clock=42, enabled=False)
        self.recovery.observe(data, result)
        self.assertEqual(calls, [(0, 0)])
        row = self.rows('sample')[-1]
        self.assertEqual((row['intake_pwm'], row['exhaust_pwm']), (0., 0.))
        self.assertTrue(row['measured_at'].startswith('2026-'))
        smoke.close()

    def test_smoke_module_separates_monotonic_and_event_clocks(self):
        path = Path(self.temp.name)/'standalone.sqlite3'
        smoke = SmokeFan(event_db=path)
        smoke.step(100, now=42, event_time=BASE)
        smoke.close()
        with sqlite3.connect(path) as db:
            event = json.loads(db.execute("SELECT payload FROM queue WHERE kind='event' ORDER BY id LIMIT 1").fetchone()[0])
        self.assertTrue(event['started_at'].startswith('2026-'))

    def test_temperature_and_pm_simultaneously_remain_separate_categories(self):
        thermal=PMRecovery(Path(self.temp.name)/'thermal.sqlite3','test-device',category='temperature')
        try:
            data=sample(0,100,temperature_c=26.)
            self.recovery.observe(data,{'sensorFresh':True});thermal.observe(data,{'sensorFresh':True})
            self.assertEqual(self.recovery.recorder.ev['status'],'active')
            self.assertEqual(thermal.recorder.ev['status'],'active')
            self.assertNotEqual(self.recovery.recorder.ev['event_id'],thermal.recorder.ev['event_id'])
            for t in range(1,12):thermal.observe(sample(t,5,temperature_c=25.),{'sensorFresh':True})
            rows=[json.loads(r[0]) for r in thermal.sink.db.execute("SELECT payload FROM queue WHERE kind='event'")]
            self.assertEqual(rows[-1]['status'],'completed')
            self.assertEqual(rows[-1]['category'],'temperature')
            self.assertEqual(rows[-1]['recovery_seconds'],11.)
            config=json.loads(rows[-1]['config_json'])
            self.assertEqual(config['recovery_operator'],'<')
            samples=[json.loads(r[0]) for r in thermal.sink.db.execute("SELECT payload FROM queue WHERE kind='sample'")]
            self.assertEqual(samples[-1]['pm25'],5)
            self.assertEqual(samples[-1]['temperature'],25.)
        finally:thermal.close()

    def test_event_photos_use_only_existing_fresh_frames(self):
        import os,time
        from event_photos import EventPhotos
        frame=Path(self.temp.name)/'camera.jpg'
        frame.write_bytes(b'\xff\xd8test\xff\xd9')
        now=time.time();os.utime(frame,(now,now))
        self.recovery.recorder.photos=EventPhotos(frame,Path(self.temp.name))
        data=sample(0);data['sensor']['timestamp']=now-.1
        self.recovery.observe(data,{'sensorFresh':True})
        data['sensor']['timestamp']=now+.1
        self.recovery.observe(data,{'sensorFresh':True})
        photo=self.rows('photo')[0]
        self.assertEqual(photo['stage'],'progress')
        self.assertTrue((Path(self.temp.name)/photo['relative_path']).is_file())
        os.utime(frame,(now-20,now-20))
        self.assertIsNone(self.recovery.recorder.photos.capture(photo['event_id'],'recovered'))

    def test_camera_cache_rejects_stale_source(self):
        import io
        from recovery_camera import RecoveryCamera
        cache=RecoveryCamera('http://camera.test',Path(self.temp.name)/'frame.jpg')
        with patch('recovery_camera.urlopen',return_value=io.BytesIO(b'{"cameraOk":false,"ageSec":1}')) as get:
            self.assertFalse(cache.update());self.assertEqual(get.call_count,1)
        with patch('recovery_camera.urlopen',side_effect=[io.BytesIO(b'{"cameraOk":true,"ageSec":0.1}'),io.BytesIO(b'\xff\xd8test\xff\xd9')]):
            self.assertTrue(cache.update());self.assertTrue(cache.target.is_file())


class FakeConnection:
    def __init__(self, fail=None):
        self.fail = fail
        self.statements = []
        self.committed = self.rolled_back = self.closed = False
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, values):
        if self.fail == 'execute': raise OSError('execute failed')
        self.statements.append((sql, values))
    def commit(self):
        if self.fail == 'commit': raise OSError('commit failed')
        self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'pending.sqlite3'
        self.sink = QueueSink(self.path)
        self.sink.put('event', {'event_id': 'abc', 'category': 'pm25', 'config_json': '{"category":"pm25"}'})
        self.sink.put('sample', {'event_id': 'abc', 'sample_id': 'xyz'})
    def tearDown(self):
        self.sink.close()
        self.temp.cleanup()
    def count(self): return self.sink.db.execute('SELECT COUNT(*) FROM queue').fetchone()[0]

    def test_relocated_connection_uses_sibling_config_and_explicit_env(self):
        import aiven_connection
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(aiven_connection.__file__).resolve().parent, root)
        conn = FakeConnection()
        with patch.object(aiven_connection, 'connect', return_value=conn) as connect:
            self.assertIs(uploader.connect(), conn)
            connect.assert_called_once_with(root/'runtime/aiven/.env')
            connect.reset_mock()
            env_file = Path(self.temp.name)/'server.env'
            self.assertIs(uploader.connect(env_file), conn)
            connect.assert_called_once_with(env_file)

    def test_commit_before_delete_and_idempotent_sql(self):
        conn = FakeConnection()
        self.assertEqual(uploader.flush(self.sink.db, conn), 2)
        self.assertTrue(conn.committed)
        self.assertEqual(self.count(), 0)
        self.assertIn('e4_recovery_events', conn.statements[0][0])
        self.assertIn('e4_recovery_samples', conn.statements[1][0])
        for sql, values in conn.statements:
            self.assertIn('ON DUPLICATE KEY UPDATE', sql)
            self.assertIn('%s', sql)

    def test_execute_or_commit_failure_keeps_entire_batch(self):
        for stage in ('execute', 'commit'):
            conn = FakeConnection(stage)
            with self.assertRaises(OSError): uploader.flush(self.sink.db, conn)
            self.assertTrue(conn.rolled_back)
            self.assertEqual(self.count(), 2)

    def test_once_failure_exit_and_connection_cleanup(self):
        conn = FakeConnection('commit')
        with patch.object(uploader, 'connect', return_value=conn):
            self.assertEqual(uploader.main([str(self.path), '--once']), 1)
        self.assertTrue(conn.closed)
        self.assertEqual(self.count(), 2)

    def test_queue_lock_rejects_second_uploader(self):
        with uploader.queue_lock(self.path):
            with self.assertRaises(OSError):
                with uploader.queue_lock(self.path): pass

    def test_aiven_zip_queue_format_and_utc_normalization(self):
        event=uploader.normalize_payload('event',dict(config_json={'thresholds':{'temperature':26}},
            event_type='temperature',started_at='2026-10-18T15:00:00+09:00'))
        self.assertEqual(json.loads(event['config_json'])['event_type'],'temperature')
        self.assertEqual(str(event['started_at']),'2026-10-18 06:00:00')
        sample=uploader.normalize_payload('sample',dict(valid={'pm25':False},measured_at='2026-10-18T06:00:00Z'))
        self.assertFalse(json.loads(sample['valid_json'])['pm25'])
        self.assertIsNone(sample['measured_at'].tzinfo)

    def test_photo_metadata_upload_uses_existing_photo_table(self):
        self.sink.put('photo',dict(photo_id='photo',event_id='abc',captured_at='2026-10-18T06:00:00Z',
                                  stage='start',relative_path='photos/abc/photo.jpg'))
        conn=FakeConnection()
        self.assertEqual(uploader.flush(self.sink.db,conn),3)
        self.assertIn('e4_recovery_photos',conn.statements[-1][0])
        self.assertEqual(self.count(),0)


if __name__ == '__main__': unittest.main()
