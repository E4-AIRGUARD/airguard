#!/usr/bin/env python3
"""train_yolo.py — build_yolo_dataset.py로 만든 dataset/data.yaml로 YOLO 모델을 학습.

사용:
  python train_yolo.py --data dataset/data.yaml --epochs 100 --imgsz 1280

CPU만 있으면 느리다 — 먼저 --epochs 20 --imgsz 640 정도로 짧게 돌려서
파이프라인이 끝까지 도는지부터 확인하는 걸 권장한다.
"""
from __future__ import annotations

import argparse
import sys

from ultralytics import YOLO

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="dataset/data.yaml")
    p.add_argument("--model", default="yolo11n.pt", help="베이스 모델 (기본: yolo11n.pt, 가장 가볍고 빠름)")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--imgsz", type=int, default=1280, help="Innomaker 원본 해상도(1280x720)에 맞춤")
    p.add_argument("--batch", type=int, default=8)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    model = YOLO(args.model)
    model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch)


if __name__ == "__main__":
    main()
