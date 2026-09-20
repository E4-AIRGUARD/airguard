#!/usr/bin/env python3
"""test_camera.py — ESP32/센서 없이 웹캠만 단독으로 테스트 (노트북에 연결해서 화면으로 확인)
준비: pip install opencv-python
사용:
  python pc/test_camera.py                    (기본 카메라 index 0)
  python pc/test_camera.py --camera-index 1    (엉뚱한 카메라가 열리면 index 바꿔보기)

화면이 뜨면:
  s = 지금 화면을 dataset/camera_test/ 폴더에 jpg로 저장
  q = 종료
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import cv2


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        sys.exit(f"웹캠(index={index})을 열 수 없습니다 → 다른 --camera-index 시도 또는 장치관리자에서 확인")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera-index", type=int, default=1, help="웹캠 장치 인덱스 (기본 0)")
    p.add_argument("--out-dir", default="dataset/camera_test", help="s로 저장할 폴더 (기본 dataset/camera_test)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)

    print(f"[test_camera] 웹캠(index={args.camera_index}) 여는 중...")
    cap = open_camera(args.camera_index)
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"[test_camera] 해상도 {actual_w:.0f}x{actual_h:.0f} — 화면 창에서 s=저장, q=종료")

    saved = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[test_camera] !! 프레임을 못 읽었습니다 (카메라 연결 확인)")
                break

            cv2.imshow("innomaker U20CAM-720p test (s=save, q=quit)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                out_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{datetime.now():%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(out_dir / fname), frame)
                saved += 1
                print(f"[test_camera] 저장됨 -> {out_dir / fname} (총 {saved}장)")
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"[test_camera] 종료 — 저장한 이미지 {saved}장")


if __name__ == "__main__":
    main()
