#!/usr/bin/env python3
"""build_yolo_dataset.py — labelme 라벨(JSON)을 YOLO 학습용 데이터셋으로 변환.

절차:
  1) part_feature_test.py의 'c' 키 등으로 이미지를 captures/full/ 에 모은다.
  2) labelme captures/full --output labelme_annotations --nodata 로 각 이미지에
     사각형(rectangle) 라벨을 그린다. 라벨 이름 = 부품 종류(class name),
     예: white_short, white_long, gray_common.
  3) 이 스크립트로 변환 + train/val 분할 + dataset/data.yaml 생성.

사용:
  python build_yolo_dataset.py --images captures/full --labels labelme_annotations --out dataset

주의: 사각형(rectangle) 라벨만 지원한다. 원형/장식 부품도 일단 외접 사각형(bounding box)으로
잡아서 검출 단계까지만 먼저 확인하고, 정밀한 외곽선 구분이 필요해지면 별도로 세그멘테이션을 검토한다.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import yaml

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

Box = tuple[str, float, float, float, float]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", required=True, help="원본 이미지 폴더")
    p.add_argument("--labels", required=True, help="labelme JSON 폴더 (이미지와 같은 파일명 stem)")
    p.add_argument("--out", default="dataset", help="YOLO 데이터셋 출력 폴더 (기본 dataset)")
    p.add_argument("--val-ratio", type=float, default=0.15, help="검증셋 비율 (기본 0.15)")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_labelme_shapes(json_path: Path) -> tuple[int, int, list[Box]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    img_w, img_h = data["imageWidth"], data["imageHeight"]
    boxes: list[Box] = []
    for shape in data.get("shapes", []):
        label = shape["label"]
        xs = [pt[0] for pt in shape["points"]]
        ys = [pt[1] for pt in shape["points"]]
        x_min, x_max = max(min(xs), 0), min(max(xs), img_w)
        y_min, y_max = max(min(ys), 0), min(max(ys), img_h)
        if x_max <= x_min or y_max <= y_min:
            continue
        boxes.append((label, x_min, y_min, x_max, y_max))
    return img_w, img_h, boxes


def to_yolo_line(class_id: int, img_w: int, img_h: int, x_min: float, y_min: float, x_max: float, y_max: float) -> str:
    x_center = (x_min + x_max) / 2 / img_w
    y_center = (y_min + y_max) / 2 / img_h
    width = (x_max - x_min) / img_w
    height = (y_max - y_min) / img_h
    return f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"


def find_image(images_dir: Path, stem: str) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png"):
        candidate = images_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def main() -> None:
    args = parse_args()
    images_dir = Path(args.images)
    labels_dir = Path(args.labels)
    out_dir = Path(args.out)

    json_paths = sorted(labels_dir.glob("*.json"))
    if not json_paths:
        raise SystemExit(f"{labels_dir}에 labelme JSON이 없습니다. 먼저 labelme로 라벨링하세요.")

    class_names: list[str] = []
    parsed: list[tuple[Path, int, int, list[Box]]] = []
    for json_path in json_paths:
        img_w, img_h, boxes = load_labelme_shapes(json_path)
        if not boxes:
            print(f"[build_yolo_dataset] 경고: {json_path.name}에 박스가 없습니다 — 건너뜀")
            continue
        for label, *_ in boxes:
            if label not in class_names:
                class_names.append(label)
        parsed.append((json_path, img_w, img_h, boxes))

    if not parsed:
        raise SystemExit("변환할 유효한 라벨이 없습니다.")

    class_names.sort()
    class_index = {name: i for i, name in enumerate(class_names)}
    print(f"[build_yolo_dataset] 클래스 {len(class_names)}종: {class_names}")

    random.seed(args.seed)
    random.shuffle(parsed)
    n_val = max(1, int(len(parsed) * args.val_ratio)) if len(parsed) > 1 else 0
    val_set = {p for p, *_ in parsed[:n_val]}

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    n_train = 0
    n_val_written = 0
    for json_path, img_w, img_h, boxes in parsed:
        stem = json_path.stem
        image_path = find_image(images_dir, stem)
        if image_path is None:
            print(f"[build_yolo_dataset] 경고: {stem}에 대응하는 이미지를 못 찾음 — 건너뜀")
            continue

        split = "val" if json_path in val_set else "train"
        n_val_written += split == "val"
        n_train += split == "train"
        shutil.copy2(image_path, out_dir / "images" / split / image_path.name)

        lines = [
            to_yolo_line(class_index[label], img_w, img_h, x_min, y_min, x_max, y_max)
            for label, x_min, y_min, x_max, y_max in boxes
        ]
        (out_dir / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    data_yaml = {
        "path": str(out_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "names": {i: name for i, name in enumerate(class_names)},
    }
    (out_dir / "data.yaml").write_text(yaml.safe_dump(data_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"[build_yolo_dataset] 완료 — train {n_train}장 / val {n_val_written}장 -> {out_dir}/data.yaml")


if __name__ == "__main__":
    main()
