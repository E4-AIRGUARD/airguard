"""Background camera snapshots for thermal experiments (no serial/fan control)."""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


class CameraRecorder:
    def __init__(self, directory: Path, index: int = 1, interval: float = 1.0):
        self.directory = Path(directory)
        self.index = index
        self.interval = interval
        self.count = 0
        self.error = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._recording = threading.Event()
        self._capture_lock = threading.Lock()
        self._start = 0.0
        self._thread = threading.Thread(target=self._run, name='thermal-camera', daemon=True)

    def open(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        self._thread.start()
        if not self._ready.wait(15):
            self._stop.set()
            raise OSError('카메라 준비 시간 초과(15초)')
        self.check()

    def begin(self):
        self._start = time.monotonic()
        self._recording.set()

    def pause(self):
        """Return only after any in-flight write ends; preview reads keep running."""
        with self._capture_lock:
            self._recording.clear()

    def resume(self):
        """Resume snapshots, preserving the first begin() timestamp."""
        self._recording.set()

    def check(self):
        if self.error:
            raise OSError(f'카메라 기록 실패: {self.error}')

    def close(self):
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise OSError('카메라 종료 시간 초과; 드라이버 응답을 확인하세요')
        self.check()

    def _run(self):
        cap = None
        try:
            import cv2
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY)
            if not cap.isOpened():
                raise OSError(f'카메라 index={self.index}를 열 수 없습니다')
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            # 계속 읽어서 오래된 버퍼 프레임이 쌓이지 않도록 한다.
            next_capture = 0.0
            with (self.directory / 'images.jsonl').open('x', encoding='utf-8') as log:
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    acquired = time.monotonic()
                    timestamp = datetime.now()
                    if not ok or frame is None:
                        raise OSError('영상 프레임 수신 실패')
                    self._ready.set()
                    if (not self._recording.is_set() or acquired < self._start
                            or acquired < next_capture or self._stop.is_set()):
                        continue
                    with self._capture_lock:
                        if not self._recording.is_set() or self._stop.is_set():
                            continue
                        # 측정 시작 기준 일정에 맞추고, 지연된 회차는 중복 촬영하지 않는다.
                        next_capture = self._start + (math.floor((acquired - self._start) / self.interval) + 1) * self.interval
                        filename = f'{self.count + 1:06d}_{timestamp:%Y%m%d_%H%M%S_%f}.jpg'
                        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                        if not ok:
                            raise OSError('JPEG 인코딩 실패')
                        with (self.directory / filename).open('xb') as image_file:
                            image_file.write(encoded.tobytes())
                        log.write(json.dumps({'image':filename, 'Time':timestamp.isoformat(timespec='milliseconds'),
                                              'elapsed_sec':round(acquired - self._start, 3)}, ensure_ascii=False) + '\n')
                        log.flush()
                        self.count += 1
        except Exception as exc:
            self.error = str(exc)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception as exc:
                    self.error = self.error or str(exc)
            self._ready.set()
