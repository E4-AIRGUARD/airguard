"""Cache existing camera HTTP frames without opening a camera or blocking control."""
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.request import urlopen


class RecoveryCamera:
    def __init__(self, origin, target):
        self.origin=origin.rstrip('/'); self.target=Path(target)
        self.target.parent.mkdir(parents=True,exist_ok=True)
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True)

    def update(self):
        with urlopen(self.origin+'/api/smoke',timeout=2) as response:
            state=json.loads(response.read(1024*1024))
        age=state.get('ageSec')
        if not state.get('cameraOk') or not isinstance(age,(int,float)) or not math.isfinite(age) or not 0<=age<=2:
            return False
        captured=time.time()-age
        with urlopen(self.origin+'/frame.jpg',timeout=2) as response:
            raw=response.read(10*1024*1024+1)
        if len(raw)>10*1024*1024 or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
            return False
        temporary=self.target.with_suffix('.tmp')
        temporary.write_bytes(raw)
        os.utime(temporary,(captured,captured))
        temporary.replace(self.target)
        return True

    def run(self):
        while not self.stop.is_set():
            try: self.update()
            except (OSError,ValueError,TypeError): pass
            self.stop.wait(.5)

    def start(self): self.thread.start()
    def close(self): self.stop.set();self.thread.join(timeout=5)
