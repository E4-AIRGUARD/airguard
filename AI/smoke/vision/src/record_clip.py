"""Jetson 카메라로 학습용 원본 영상을 녹화해요 (화면 표시나 글자 없이 원본 그대로).
사용법
  python3 record_clip.py --label without_hand   --seconds 60     # 연기 없음: 손, 사람, 물건이 움직이는 장면
  python3 record_clip.py --label without_empty  --seconds 60     # 연기 없음: 아무것도 없는 빈 장면
  python3 record_clip.py --label with_smoke     --seconds 80     # 연기 있음: 모기향을 피운 장면
저장 위치: ~/smoke/clips/<label>_<시각>.mp4   (--rotate 180 기본: 학습 영상과 같은 방향)
"""
import argparse, os, time
import cv2
ap = argparse.ArgumentParser()
ap.add_argument('--label', required=True); ap.add_argument('--seconds', type=int, default=60)
ap.add_argument('--source', default='0'); ap.add_argument('--rotate', type=int, default=180, choices=[0, 90, 180, 270])
ap.add_argument('--width', type=int, default=1280); ap.add_argument('--height', type=int, default=720)
ap.add_argument('--fps', type=float, default=10, help='카메라가 실제로 보내는 초당 프레임 수 (이 카메라는 10)')
ap.add_argument('--show', action='store_true', help='녹화 중 화면 보기(VNC 화면에서만)')
a = ap.parse_args()
ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
os.makedirs(os.path.expanduser('~/smoke/clips'), exist_ok=True)
path = os.path.expanduser(f"~/smoke/clips/{a.label}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
fps = a.fps; w = None; t0 = time.time(); n = 0
while time.time() - t0 < a.seconds:
    ok, f = cap.read()
    if not ok: break
    if a.rotate: f = cv2.rotate(f, ROT[a.rotate])
    if w is None: w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (f.shape[1], f.shape[0]))
    w.write(f); n += 1
    if a.show:
        cv2.imshow('rec', f)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
if w: w.release()
print('saved', path, 'frames', n, 'sec', round(time.time() - t0, 1))
