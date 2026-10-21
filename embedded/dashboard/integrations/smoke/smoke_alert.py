"""Jetson에서 카메라로 연기를 판단하고, 대시보드가 가져갈 수 있게 상태를 알려 주는 프로그램.

하는 일
  1) 카메라(또는 영상 파일)에서 프레임을 읽어 연기 점수를 내고, 데모와 같은 방식(EMA, 켜기/끄기 기준, 유지 시간)으로 '연기 있음/없음'을 정해요.
  2) 상태를 작은 웹 서버(기본 포트 8092)로 내보내요. 대시보드의 smoke-alert.js 가 1초마다 가져가서 알림을 띄워요.
       GET  /api/smoke              현재 상태와 최근 연기 기록(JSON)
       GET  /frame.jpg              가장 최근 카메라 사진
       GET  /frame_model.jpg        모델이 실제로 보는 화면(열화상 필터 적용). 연기가 모델에게 어떻게 보이는지 확인용
       GET  /event/<기록 id>.jpg    연기가 처음 감지된 순간의 사진
  3) (선택) 연기가 켜지거나 꺼질 때 다른 서버에도 알려 줘요: --post-url (HTTP POST), --ws-url (WebSocket)

사용법 (Jetson)
  python3 smoke_alert.py --model smoke_cls_v4.pt --source 0 --device 0
  python3 smoke_alert.py --model smoke_cls_v4.pt --source test.mp4 --device 0     # 영상 파일로 시험
  q 로 끄려면 --show (VNC 화면에서만), 터미널에서는 Ctrl+C

기본값: 켜기 기준 0.50, 끄기 기준 0.30 (v4, cmp2 영상에서 오탐 0초였던 값), EMA 0.3, 유지 2초, 3프레임마다 판단, 카메라 180도 회전(학습 영상과 같은 방향),
        해제 알림은 판정이 4초 동안 꺼져 있은 뒤에 보내요(연기가 잠깐 옅어져도 알림이 껐다 켜지지 않게)
"""
import argparse, asyncio, json, os, queue, re, threading, time, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

KST = timezone(timedelta(hours=9))
ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


class SmokeJudge:
    """realtime_demo_cls.py 와 같은 판정기. 프레임 점수를 받아 깜빡임이 적은 상태를 돌려줘요."""
    def __init__(self, on=0.50, off=0.30, alpha=0.3, hold=2.0):
        self.on, self.off, self.alpha, self.hold = on, off, alpha, hold
        self.ema, self.state, self.t_on = None, False, -1e9

    def update(self, score, t):
        self.ema = score if self.ema is None else self.alpha * score + (1 - self.alpha) * self.ema
        if not self.state and self.ema >= self.on: self.state, self.t_on = True, t
        elif self.state and self.ema < self.off and (t - self.t_on) >= self.hold: self.state = False
        if self.state and self.ema >= self.on: self.t_on = t
        return self.state, self.ema


_clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))
def thermal(bgr):
    return cv2.applyColorMap(_clahe.apply(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)), cv2.COLORMAP_INFERNO)


def iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), KST).isoformat(timespec='seconds')


class Shared:
    """판단 루프와 웹 서버가 같이 쓰는 상태."""
    def __init__(self, device_id, model_name, keep=30):
        self.lock = threading.Lock()
        self.device_id, self.model_name, self.keep = device_id, model_name, keep
        self.smoke, self.raw, self.score, self.avg, self.motion = False, False, 0.0, 0.0, 0.0
        self.updated, self.camera_ok, self.started = 0.0, False, time.time()
        self.incidents, self.snapshots, self.frame_jpg, self.model_jpg, self.counter = [], {}, None, None, 0

    def snapshot(self):
        with self.lock:
            return {
                'deviceId': self.device_id, 'model': self.model_name,
                'smoke': self.smoke, 'raw': self.raw, 'motion': round(self.motion, 2), 'score': round(self.score, 3), 'avg': round(self.avg, 3),
                'updated': iso(self.updated) if self.updated else None, 'ageSec': round(time.time() - self.updated, 1) if self.updated else None,
                'cameraOk': self.camera_ok, 'uptimeSec': round(time.time() - self.started),
                'current': self.incidents[-1] if self.incidents and self.incidents[-1]['end'] is None else None,
                'incidents': list(reversed(self.incidents[-self.keep:])),
            }

    def start_incident(self, score, jpg):
        with self.lock:
            self.counter += 1
            inc = {'id': f'SMOKE-{datetime.now(KST):%Y%m%d-%H%M%S}-{self.counter}', 'start': iso(), 'end': None, 'peak': round(score, 3), 'durationSec': None}
            self.incidents.append(inc)
            if jpg is not None: self.snapshots[inc['id']] = jpg
            for k in list(self.snapshots):
                if k not in {i['id'] for i in self.incidents[-self.keep:]}: del self.snapshots[k]
            return dict(inc)

    def end_incident(self):
        with self.lock:
            if not self.incidents or self.incidents[-1]['end'] is not None: return None
            inc = self.incidents[-1]
            inc['end'] = iso()
            inc['durationSec'] = round(time.time() - datetime.fromisoformat(inc['start']).timestamp())
            return dict(inc)

    def bump_peak(self, score):
        with self.lock:
            if self.incidents and self.incidents[-1]['end'] is None:
                self.incidents[-1]['peak'] = max(self.incidents[-1]['peak'], round(score, 3))


def make_handler(shared):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', '*')            # 대시보드(다른 주소)에서 읽을 수 있게
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self): self._send(204, b'', 'text/plain')

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/api/smoke':
                return self._send(200, json.dumps(shared.snapshot(), ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            if path == '/frame.jpg':
                with shared.lock: jpg = shared.frame_jpg
                return self._send(200, jpg, 'image/jpeg') if jpg else self._send(404, b'no frame', 'text/plain')
            if path == '/frame_model.jpg':        # 모델이 실제로 보는 화면(열화상 필터 적용)
                with shared.lock: jpg = shared.model_jpg
                return self._send(200, jpg, 'image/jpeg') if jpg else self._send(404, b'no frame', 'text/plain')
            m = re.fullmatch(r'/event/([\w-]+)\.jpg', path)
            if m:
                with shared.lock: jpg = shared.snapshots.get(m.group(1))
                return self._send(200, jpg, 'image/jpeg') if jpg else self._send(404, b'no snapshot', 'text/plain')
            if path == '/':
                return self._send(200, b'smoke_alert running. GET /api/smoke', 'text/plain')
            self._send(404, b'not found', 'text/plain')
    return H


class Notifier:
    """연기가 켜지거나 꺼질 때 다른 서버에도 알려요. 네트워크가 느려도 판단 루프는 멈추지 않아요(별도 스레드)."""
    def __init__(self, post_url=None, ws_url=None):
        self.post_url, self.ws_url = post_url, ws_url
        self.q = queue.Queue(maxsize=100)
        if post_url or ws_url: threading.Thread(target=self._run, daemon=True).start()

    def send(self, payload):
        try: self.q.put_nowait(payload)
        except queue.Full: pass

    def _post(self, payload):
        req = urllib.request.Request(self.post_url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            urllib.request.urlopen(req, timeout=3).read()
            print('[알림 POST] 보냄', payload['event'])
        except Exception as e:
            print('[알림 POST] 실패:', e)

    async def _ws_loop(self, items):
        import websockets
        conn = None
        while True:
            payload = await asyncio.get_event_loop().run_in_executor(None, items.get)
            for attempt in range(3):
                try:
                    if conn is None: conn = await websockets.connect(self.ws_url)
                    await conn.send(json.dumps(payload, ensure_ascii=False))
                    print('[알림 WS] 보냄', payload['event'])
                    break
                except Exception as e:
                    print('[알림 WS] 실패, 다시 시도:', e)
                    conn = None
                    await asyncio.sleep(1)

    def _run(self):
        ws_items = queue.Queue()
        if self.ws_url:
            threading.Thread(target=lambda: asyncio.run(self._ws_loop(ws_items)), daemon=True).start()
        while True:
            payload = self.q.get()
            if self.post_url: self._post(payload)
            if self.ws_url: ws_items.put(payload)


def open_camera(a):
    cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
    if a.width and a.height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True); ap.add_argument('--source', default='0')
    ap.add_argument('--device', default='cpu'); ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--on', type=float, default=0.50); ap.add_argument("--off", type=float, default=0.30)
    ap.add_argument('--alpha', type=float, default=0.3); ap.add_argument('--hold', type=float, default=2.0)
    ap.add_argument('--stride', type=int, default=3, help='N프레임마다 1번 판단')
    ap.add_argument('--rotate', type=int, default=180, choices=[0, 90, 180, 270])
    ap.add_argument('--width', type=int, default=1280); ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--host', default='0.0.0.0'); ap.add_argument('--port', type=int, default=8092)
    ap.add_argument('--device-id', default=os.uname().nodename if hasattr(os, 'uname') else 'jetson')
    ap.add_argument('--post-url', default='', help='연기 켜짐/꺼짐 때 JSON을 POST할 주소 (선택)')
    ap.add_argument('--ws-url', default='', help='연기 켜짐/꺼짐 때 JSON을 보낼 WebSocket 주소 (선택, websockets 필요)')
    ap.add_argument('--clear-sec', type=float, default=4, help='판정이 꺼진 뒤 이 시간(초) 동안 다시 켜지지 않아야 "연기 해제"로 알림 (깜빡임 방지)')
    ap.add_argument('--motion-gate', action='store_true', help='새로 알림을 켤 때 최근 3초 안에 화면에 움직임이 있어야 함 (가만히 놓인 모기향 오탐 방지, 시험용)')
    ap.add_argument('--motion-min', type=float, default=4.0, help='움직임 기준 (1초 간격 프레임 차이 평균, 빈 화면은 약 2, 연기는 약 5~30)')
    ap.add_argument('--repeat-sec', type=float, default=0, help='연기가 계속되는 동안 N초마다 알림을 다시 보냄 (0=안 보냄)')
    ap.add_argument('--show', action='store_true'); ap.add_argument('--max-frames', type=int, default=0)
    ap.add_argument('--realtime', action='store_true', help='영상 파일을 실제 속도로 재생하며 판단 (시험용)')
    a = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(a.model, task='classify')
    sm = [k for k, v in model.names.items() if v == 'smoke'][0]
    shared = Shared(a.device_id, os.path.basename(a.model))
    notifier = Notifier(a.post_url or None, a.ws_url or None)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(shared))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'[smoke_alert] http://{a.host}:{a.port}/api/smoke  기준 켜기 {a.on} / 끄기 {a.off}, EMA {a.alpha}, 유지 {a.hold}초')

    is_file = not a.source.isdigit()
    cap = open_camera(a)
    judge = SmokeJudge(a.on, a.off, a.alpha, a.hold)
    fps = (cap.get(cv2.CAP_PROP_FPS) or 30) if is_file else 30
    t0 = time.monotonic(); i = 0; last_repeat = 0.0; fails = 0
    alarm, off_since = False, None
    hist, mot_hist, motion_now = [], [], 0.0

    def event(name, inc):
        return {'type': 'SMOKE_ALERT', 'event': name, 'deviceId': a.device_id, 'incidentId': inc['id'],
                'time': inc['end'] if name == 'SMOKE_CLEARED' else inc['start'], 'score': shared.score, 'avg': shared.avg,
                'peak': inc['peak'], 'durationSec': inc['durationSec'], 'model': shared.model_name}

    try:
        while True:
            ok, f = cap.read()
            if not ok:
                if is_file: break                      # 영상 파일은 끝나면 종료
                fails += 1
                with shared.lock: shared.camera_ok = False
                if fails % 20 == 1: print('[smoke_alert] 카메라 프레임을 못 읽음, 다시 연결 시도')
                cap.release(); time.sleep(1); cap = open_camera(a)
                continue
            fails = 0
            if a.max_frames and i >= a.max_frames: break
            if a.rotate: f = cv2.rotate(f, ROT[a.rotate])
            if i % a.stride == 0:
                small = cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA)
                tin = thermal(small)
                g = cv2.GaussianBlur(_clahe.apply(cv2.resize(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (320, 180), interpolation=cv2.INTER_AREA)), (0, 0), 2).astype('float32')
                score = float(model.predict(tin, imgsz=a.imgsz, verbose=False, device=a.device)[0].probs.data[sm])
                model_jpg = cv2.imencode('.jpg', tin, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                now = (i / fps) if is_file else (time.monotonic() - t0)
                hist.append((now, g))                           # 1초 전 화면과 비교해 움직임 크기를 잼
                while len(hist) > 1 and hist[1][0] <= now - 1.0: hist.pop(0)
                motion_now = float(np.abs(g - hist[0][1]).mean()) if now - hist[0][0] >= 0.9 else 0.0
                mot_hist.append((now, motion_now))
                while mot_hist and mot_hist[0][0] < now - 3.0: mot_hist.pop(0)
                motion_ok = max(m for _, m in mot_hist) >= a.motion_min
                state, ema = judge.update(score, now)
                if state: off_since = None
                elif alarm and off_since is None: off_since = now
                new_alarm = alarm
                if state and (alarm or motion_ok or not a.motion_gate): new_alarm = True   # 켤 때만 움직임 확인, 켜진 뒤에는 유지
                elif alarm and now - off_since >= a.clear_sec: new_alarm = False   # 꺼진 채로 clear_sec 지속돼야 해제
                thumb = cv2.imencode('.jpg', cv2.resize(f, (640, int(f.shape[0] * 640 / f.shape[1]))), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                with shared.lock:
                    shared.smoke, shared.raw, shared.score, shared.avg, shared.motion = new_alarm, state, score, ema, motion_now
                    shared.updated, shared.camera_ok, shared.frame_jpg, shared.model_jpg = time.time(), True, thumb, model_jpg
                if new_alarm and not alarm:
                    inc = shared.start_incident(score, thumb); last_repeat = now
                    print(f'[smoke_alert] 연기 감지  점수 {score:.2f} 평균 {ema:.2f}  {inc["id"]}')
                    notifier.send(event('SMOKE_DETECTED', inc))
                elif new_alarm:
                    shared.bump_peak(score)
                    if a.repeat_sec and now - last_repeat >= a.repeat_sec:
                        last_repeat = now
                        notifier.send(event('SMOKE_DETECTED', shared.snapshot()['current']))
                elif alarm and not new_alarm:
                    inc = shared.end_incident()
                    print(f'[smoke_alert] 연기 해제  {inc["id"]}  {inc["durationSec"]}초')
                    notifier.send(event('SMOKE_CLEARED', inc))
                alarm = new_alarm
                if a.realtime and is_file:
                    time.sleep(max(0.0, (i + 1) / fps - (time.monotonic() - t0)))
            if a.show:
                cv2.imshow('smoke_alert', f)
                if cv2.waitKey(1) & 0xFF == ord('q'): break
            i += 1
    except KeyboardInterrupt:
        pass
    finally:
        cap.release(); srv.shutdown()
        print('[smoke_alert] 종료')


if __name__ == '__main__':
    main()
