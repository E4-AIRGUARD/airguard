"""Persist camera-owned smoke incidents through the existing upload queue.

The camera server owns detection/recovery. No PM thresholds or fan commands here.
HTTP and SQLite work run on a separate thread, never in the control loop.
"""
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from event_recorder import QueueSink


def utc(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Camera timestamps must include a timezone')
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


class VisionRecorder:
    def __init__(self, path, device_id):
        self.sink = QueueSink(path)
        self.root = Path(path).parent
        self.device_id = device_id
        self.sink.db.execute('CREATE TABLE IF NOT EXISTS vision_status (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.sink.db.commit()
        self.events = {row['event_id']: row for (payload,) in self.sink.db.execute(
            'SELECT payload FROM vision_status') for row in [json.loads(payload)]}

    def ingest(self, data, photo_fetch):
        age = data.get('ageSec')
        if not data.get('cameraOk') or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= 5:
            return
        incidents = {r['id']: r for r in data.get('incidents', []) if r.get('id')}
        if data.get('current'): incidents[data['current']['id']] = data['current']
        for source in incidents.values():
            start = utc(source['start'])
            end = utc(source['end']) if source.get('end') else None
            if end and end < start: continue
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.device_id+'/camera/'+source['id']+'/'+start))
            old = self.events.get(event_id, {})
            if old.get('status') == 'completed' and not end: continue
            row = dict(event_id=event_id, device_id=self.device_id, category='smoke', started_at=start,
                first_reached_at=end, recovered_at=end, recording_ended_at=end,
                status='completed' if end else 'active',
                recovery_seconds=(datetime.fromisoformat(end.replace('Z','+00:00'))-datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds() if end else None)
            if any(old.get(k) != v for k, v in row.items()):
                config = dict(category='smoke', source='camera_smoke_detector', camera_event_id=source['id'],
                    camera_device_id=data.get('deviceId'), model=data.get('model'), peak_score=source.get('peak'))
                self.sink.put('event', dict(row, config_json=json.dumps(config)))
            # Original detector image can be recovered even for past incidents.
            row['photo_saved'] = old.get('photo_saved', False)
            if not row['photo_saved']:
                try:
                    raw = photo_fetch(source['id'])
                    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9') or len(raw)>10*1024*1024:
                        raise ValueError('Invalid JPEG')
                    photo_id = str(uuid.uuid5(uuid.UUID(event_id), 'camera-start'))
                    relative = Path('photos')/event_id/(photo_id+'.jpg')
                    target = self.root/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix('.tmp')
                    temporary.write_bytes(raw); temporary.replace(target)
                    self.sink.put('photo', dict(photo_id=photo_id,event_id=event_id,captured_at=start,
                        stage='start',relative_path=relative.as_posix()))
                    row['photo_saved'] = True
                except (OSError, ValueError):
                    pass
            if row != old:
                self.events[event_id] = row
                self.sink.db.execute('INSERT OR REPLACE INTO vision_status VALUES (?, ?)', (event_id,json.dumps(row)))
                self.sink.db.commit()

    def snapshot(self):
        return [{k:v for k,v in row.items() if k!='photo_saved'} for row in
            sorted(self.events.values(), key=lambda r:r['started_at'])[-100:]]

    def close(self): self.sink.close()


class VisionRecovery:
    def __init__(self, origin, path, device_id):
        self.origin, self.path, self.device_id = origin.rstrip('/'), path, device_id
        self.stop = threading.Event()
        self.events = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def fetch_photo(self, source_id):
        with urlopen(self.origin+'/event/'+quote(source_id,safe='')+'.jpg', timeout=2) as response:
            return response.read(10*1024*1024+1)

    def run(self):
        recorder = VisionRecorder(self.path, self.device_id)
        self.events = recorder.snapshot()
        last_warning = 0
        try:
            while not self.stop.is_set():
                try:
                    with urlopen(self.origin+'/api/smoke', timeout=2) as response:
                        data = json.loads(response.read(1024*1024))
                    recorder.ingest(data, self.fetch_photo)
                    self.events = recorder.snapshot()
                except (OSError, ValueError, TypeError, KeyError) as error:
                    if time.time()-last_warning>60:
                        print('Camera event sync retry:', type(error).__name__, flush=True)
                        last_warning=time.time()
                self.stop.wait(1)
        finally:
            recorder.close()

    def start(self): self.thread.start()
    def snapshot(self): return self.events
    def close(self): self.stop.set(); self.thread.join(timeout=5)
