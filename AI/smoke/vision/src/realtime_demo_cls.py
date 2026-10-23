"""분류 모델 실시간 연기 판별 데모. 원본 화면 위에 '연기 있음/없음'과 점수를 표시해요.
열화상 필터(CLAHE+INFERNO)는 모델 입력에만 쓰고, 화면에는 원본을 보여 줘요.

사용법
  python realtime_demo_cls.py --model models/smoke_cls_v4.pt --source 0                      # 웹캠
  python realtime_demo_cls.py --model models/smoke_cls_v4.pt --source 0 --device 0 --rotate 180   # Jetson(GPU) + 카메라가 거꾸로 달린 경우
  python realtime_demo_cls.py --model models/smoke_cls_v4.pt --source video.mp4              # 영상 파일
  python realtime_demo_cls.py --model models/smoke_cls_v4.pt --source video.mp4 --save out.mp4 --no-show
키: q 종료

깜빡임을 줄이는 방법 (모두 켜져 있어요)
  1) 점수 평균: 최근 점수를 지수 이동 평균(--alpha)으로 섞어요.
  2) 켜기/끄기 기준 분리: --on(기본 0.40) 이상이면 켜고, --off(기본 0.20) 미만이어야 꺼요.
  3) 유지 시간: 한 번 켜지면 최소 --hold(기본 2초)는 유지해요.
"""
import argparse, time
import cv2, numpy as np
from ultralytics import YOLO

class SmokeJudge:
    """프레임 점수를 받아 깜빡임이 적은 '연기 있음/없음' 상태를 돌려줘요."""
    def __init__(self, on=0.40, off=0.20, alpha=0.3, hold=2.0):
        self.on, self.off, self.alpha, self.hold = on, off, alpha, hold
        self.ema, self.state, self.t_on = None, False, -1e9
    def update(self, score, t):
        self.ema = score if self.ema is None else self.alpha * score + (1 - self.alpha) * self.ema
        if not self.state and self.ema >= self.on: self.state, self.t_on = True, t
        elif self.state and self.ema < self.off and (t - self.t_on) >= self.hold: self.state = False
        if self.state and self.ema >= self.on: self.t_on = t   # 연기가 계속 보이면 유지 시간을 갱신
        return self.state, self.ema

clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))
def thermal(bgr):
    return cv2.applyColorMap(clahe.apply(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)), cv2.COLORMAP_INFERNO)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True); ap.add_argument('--source', default='0')
    ap.add_argument('--on', type=float, default=0.40); ap.add_argument('--off', type=float, default=0.20)
    ap.add_argument('--alpha', type=float, default=0.3); ap.add_argument('--hold', type=float, default=2.0)
    ap.add_argument('--stride', type=int, default=3, help='N프레임마다 1번 판단 (속도용)')
    ap.add_argument('--imgsz', type=int, default=640); ap.add_argument('--device', default='cpu')
    ap.add_argument('--rotate', type=int, default=0, choices=[0, 90, 180, 270], help='카메라가 거꾸로면 180 (학습 영상은 180도 돌린 상태였어요)')
    ap.add_argument('--width', type=int, default=0); ap.add_argument('--height', type=int, default=0)
    ap.add_argument('--save', default=''); ap.add_argument('--no-show', action='store_true'); ap.add_argument('--max-frames', type=int, default=0)
    a = ap.parse_args()
    model = YOLO(a.model, task='classify'); sm = [k for k, v in model.names.items() if v == 'smoke'][0]
    cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
    if a.width and a.height: cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30; judge = SmokeJudge(a.on, a.off, a.alpha, a.hold)
    writer = None; i = 0; score = 0.0; state = False; ema = 0.0
    while True:
        ok, f = cap.read()
        if not ok or (a.max_frames and i >= a.max_frames): break
        if a.rotate: f = cv2.rotate(f, ROT[a.rotate])
        if i % a.stride == 0:
            small = cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA)   # 학습 때와 같은 크기로 맞춰서 판단
            score = float(model.predict(thermal(small), imgsz=a.imgsz, verbose=False, device=a.device)[0].probs.data[sm])
            state, ema = judge.update(score, i / fps)
        col = (0, 0, 255) if state else (0, 200, 0)
        cv2.rectangle(f, (0, 0), (f.shape[1], 46), (0, 0, 0), -1)
        cv2.putText(f, f"{'SMOKE' if state else 'no smoke'}  score {score:.2f}  avg {ema:.2f}", (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2)
        if a.save and writer is None: writer = cv2.VideoWriter(a.save, cv2.VideoWriter_fourcc(*'mp4v'), fps, (f.shape[1], f.shape[0]))
        if writer: writer.write(f)
        if not a.no_show:
            cv2.imshow('smoke', f)
            if cv2.waitKey(1) & 0xFF == ord('q'): break
        i += 1
    if writer: writer.release()
