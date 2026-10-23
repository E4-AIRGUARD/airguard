"""Camera decisions are archived without inventing PM events or measurements."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'integrations'),str(Path(__file__).resolve().parents[1]/'integrations/smoke_fan_final')]
from vision_recovery import VisionRecorder

class VisionTests(unittest.TestCase):
    def test_archive_decisions_photos_restart_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending.sqlite3'
            recorder=VisionRecorder(path,'test-device')
            event=dict(id='SMOKE-1',start='2026-10-22T16:00:00+09:00',end=None,peak=.9)
            data=dict(cameraOk=True,ageSec=.1,deviceId='camera',incidents=[event],current=event)
            recorder.ingest(data,lambda _:b'\xff\xd8test\xff\xd9')
            active=recorder.snapshot()[0]
            self.assertEqual(active['category'],'smoke')
            self.assertEqual(active['started_at'],'2026-10-22T07:00:00Z')
            self.assertEqual(active['status'],'active')
            count=recorder.sink.db.execute('SELECT COUNT(*) FROM queue').fetchone()[0]
            self.assertEqual(count,2)
            recorder.ingest(data,lambda _:self.fail('Photo fetched twice'))
            self.assertEqual(recorder.sink.db.execute('SELECT COUNT(*) FROM queue').fetchone()[0],count)
            event['end']='2026-10-22T16:00:20+09:00'
            data['current']=None
            recorder.ingest(dict(data,cameraOk=False),lambda _:b'')
            self.assertEqual(recorder.snapshot()[0]['status'],'active','camera loss is not recovery')
            recorder.ingest(data,lambda _:b'')
            completed=recorder.snapshot()[0]
            self.assertEqual(completed['event_id'],active['event_id'])
            self.assertEqual(completed['status'],'completed')
            self.assertEqual(completed['recovery_seconds'],20)
            self.assertEqual(recorder.sink.db.execute("SELECT COUNT(*) FROM queue WHERE kind='sample'").fetchone()[0],0)
            self.assertIsNone(recorder.sink.get_state(),'must not overwrite the PM recorder state')
            self.assertEqual(len(list(Path(directory).glob('photos/*/*.jpg'))),1)
            recorder.close()
            recorder=VisionRecorder(path,'test-device')
            recorder.ingest(data,lambda _:self.fail('Photo fetched after restart'))
            self.assertEqual(recorder.snapshot(),[completed])
            self.assertEqual(recorder.sink.db.execute('SELECT COUNT(*) FROM queue').fetchone()[0],3)
            payload=json.loads(recorder.sink.db.execute("SELECT payload FROM queue WHERE kind='event' ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(json.loads(payload['config_json'])['source'],'camera_smoke_detector')
            recorder.close()

if __name__=='__main__':unittest.main()
