#!/usr/bin/env python3
"""convert_to_dcp.py — 이미지 폴더 전체를 DCP 연기맵(히트맵)으로 일괄 변환
collect_images.py의 미리보기용 DCP 필터(s 키로 확인하던 것)를 폴더 단위 배치 변환으로 재사용.
사용:
  python pc/convert_to_dcp.py --in-dir dataset/set_B --out-dir dataset/set_B_dcp
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent))
from collect_images import smoke_density_map  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in-dir", required=True, help="원본 이미지 폴더")
    p.add_argument("--out-dir", required=True, help="DCP 연기맵 출력 폴더 (파일명 그대로 유지)")
    p.add_argument("--patch-size", type=int, default=15, help="DCP dark channel 패치 크기 (기본 15)")
    p.add_argument("--omega", type=float, default=0.95, help="DCP omega (기본 0.95)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.jpg"))
    total = len(files)
    print(f"[convert_to_dcp] {in_dir} -> {out_dir} : {total}장 변환 시작")

    done = failed = 0
    for i, f in enumerate(files, 1):
        img = cv2.imread(str(f))
        if img is None:
            print(f"[convert_to_dcp] !! 읽기 실패, 건너뜀: {f.name}")
            failed += 1
            continue
        density = smoke_density_map(img, args.patch_size, args.omega)
        cv2.imwrite(str(out_dir / f.name), density)
        done += 1
        if i % 50 == 0 or i == total:
            print(f"[convert_to_dcp] {i}/{total} 완료 (실패 {failed})")

    print(f"[convert_to_dcp] 종료 -> 성공 {done}장, 실패 {failed}장 -> {out_dir}")


if __name__ == "__main__":
    main()
