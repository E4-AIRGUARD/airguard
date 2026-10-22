"""Archive fresh camera frames without opening the webcam a second time."""
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class EventPhotos:
    def __init__(self, source, root):
        self.source, self.root = Path(source), Path(root)
        self.last = {}
        self.last_warning = 0

    def capture(self, event_id, stage, started_at=None, ended_at=None):
        now = time.time()
        if stage == 'progress' and now - self.last.get(event_id, 0) < 10:
            return None
        try:
            # OpenCV publishes camera.jpg with atomic replacement.
            with self.source.open('rb') as f:
                captured = os.fstat(f.fileno()).st_mtime
                if not 0 <= now - captured <= 3:
                    return None
                if started_at and captured < datetime.fromisoformat(started_at).timestamp():
                    return None
                if ended_at and captured > datetime.fromisoformat(ended_at).timestamp():
                    return None
                data = f.read()
            if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                return None
            photo_id = str(uuid.uuid4())
            relative = Path('photos') / str(uuid.UUID(event_id)) / (photo_id + '.jpg')
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(target)
            self.last[event_id] = now
            return dict(photo_id=photo_id, event_id=event_id,
                        captured_at=datetime.fromtimestamp(captured, timezone.utc).isoformat(),
                        stage=stage, relative_path=relative.as_posix())
        except OSError as exc:
            if now - self.last_warning >= 60:
                print('Event photo unavailable:', type(exc).__name__, flush=True)
                self.last_warning = now
            return None
