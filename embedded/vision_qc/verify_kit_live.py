#!/usr/bin/env python3
"""verify_kit_live.py — 학습된 YOLO 모델로 웹캠에서 부품을 실시간 검출하고,
작업 A/B 준비 목록(kit_config.yaml)과 대조해 과부족을 표시한다.

사용:
  python verify_kit_live.py --weights runs/detect/train/weights/best.pt --task A

화면이 뜨면:
  space = 현재 프레임 기준으로 개수를 확정해서 준비 목록과 대조 (터미널에 출력)
  q = 종료
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import cv2
import yaml
from ultralytics import YOLO

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", required=True, help="학습된 가중치 경로 (best.pt)")
    p.add_argument("--config", default="kit_config.yaml", help="작업별 준비 목록 설정 파일")
    p.add_argument("--task", required=True, help="config 안의 작업 이름 (예: A, B)")
    p.add_argument("--camera-index", type=int, default=1)
    p.add_argument("--conf", type=float, default=0.5, help="검출 confidence 임계값")
    return p.parse_args()


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit(f"웹캠(index={index})을 열 수 없습니다")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    return cap


def load_expected(config_path: Path, task: str) -> dict[str, int]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    tasks = config.get("tasks", {})
    if task not in tasks:
        raise SystemExit(f"'{task}' 작업이 {config_path}에 없습니다. 정의된 작업: {list(tasks)}")
    return tasks[task]


def diff_report(expected: dict[str, int], actual: Counter) -> str:
    lines = []
    all_names = sorted(set(expected) | set(actual))
    ok = True
    for name in all_names:
        exp = expected.get(name, 0)
        got = actual.get(name, 0)
        mark = "OK" if got == exp else ("부족" if got < exp else "초과")
        if got != exp:
            ok = False
        lines.append(f"  {name}: 필요 {exp} / 검출 {got}  [{mark}]")
    header = "=== 검사 통과 ===" if ok else "=== 불일치 발견 ==="
    return header + "\n" + "\n".join(lines)


def main() -> None:
    args = parse_args()
    expected = load_expected(Path(args.config), args.task)
    model = YOLO(args.weights)

    print(f"[verify_kit_live] 웹캠(index={args.camera_index}) 여는 중...")
    cap = open_camera(args.camera_index)
    print(f"[verify_kit_live] 작업 {args.task} 준비 목록: {expected}")
    print("[verify_kit_live] space=현재 프레임으로 대조, q=종료")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[verify_kit_live] !! 프레임을 못 읽었습니다")
                break

            results = model.predict(frame, conf=args.conf, verbose=False)[0]
            annotated = results.plot()
            cv2.imshow("verify_kit_live (space=check, q=quit)", annotated)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            elif key == ord(" "):
                names = model.names
                detected = Counter(names[int(c)] for c in results.boxes.cls.tolist())
                print(diff_report(expected, detected))
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
