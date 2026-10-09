"""Live camera preview only: no image saving, serial connection, or fan control."""
import json
import os
from pathlib import Path
import time

import cv2


def main():
    root = Path(__file__).resolve().parent / 'data' / 'camera_preview'
    root.mkdir(parents=True, exist_ok=True)
    status_path = root / f'{os.getpid()}.json'
    stop_path = root / f'{os.getpid()}.stop'
    status = {'pid': os.getpid(), 'camera_index': 1, 'status': 'opening',
              'images_saved': 0, 'rotation_deg': 180}

    def save():
        status_path.write_text(json.dumps(status, indent=2), encoding='utf-8')

    cap = None
    title = 'AIRGUARD - Live Camera (no recording) - Q / Esc to close'
    save()
    try:
        cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise OSError('Cannot open camera index 1')
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, 960, 540)
        frames = 0
        last_report = 0
        while not stop_path.exists():
            ok, frame = cap.read()
            if not ok or frame is None:
                raise OSError('Camera frame unavailable')
            frame = cv2.rotate(frame, cv2.ROTATE_180)
            cv2.imshow(title, frame)
            frames += 1
            now = time.monotonic()
            if now - last_report >= 2:
                status.update(status='previewing', frames_displayed=frames,
                              last_frame_unix=time.time(), frame_shape=list(frame.shape))
                save()
                last_report = now
            if cv2.waitKey(1) & 0xFF in (27, ord('q'), ord('Q')):
                break
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
        status['status'] = 'closed'
    except Exception as exc:
        status.update(status='failed', error=str(exc))
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        save()


if __name__ == '__main__':
    main()
