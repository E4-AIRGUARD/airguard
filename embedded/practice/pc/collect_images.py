#!/usr/bin/env python3
"""collect_images.py — 웹캠 프레임만 연속 캡처해서 세트 A/B 이미지 데이터셋 수집 (센서 없이, 카메라 노트북 직결)
준비: pip install opencv-python
사용:
  python pc/collect_images.py --set A --duration-min 7   (세트 A: 평상시, 연기 없음, 5~10분)
  python pc/collect_images.py --set B --duration-min 7   (세트 B: 연기 발생, 5~10분)
  python pc/collect_images.py --set A --camera-index 0 --interval-sec 1   (외장캠이 아니라 내장캠을 쓰고 싶을 때)

세트 규칙: 세트 A는 dataset/set_A/, 세트 B는 dataset/set_B/ 폴더에 각각 저장된다 —
폴더 자체가 라벨 역할(A=0/정상, B=1/연기)을 하므로 두 세트를 같은 폴더에 섞지 않는다.
화면 미리보기 창에서 언제든 q로 중단 가능하고, 그때까지 저장된 이미지는 그대로 유지된다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp949 콘솔에서 —/· 깨짐 방지

SET_FOLDER = {"A": "set_A", "B": "set_B"}
SET_DESC = {"A": "A(평상시, 연기 없음)", "B": "B(연기 발생)"}


def enhance_smoke_contrast(frame: cv2.typing.MatLike, clahe: cv2.CLAHE) -> cv2.typing.MatLike:
    """회색 연기는 원본에서 명암 대비가 낮아 눈에 잘 안 띔 — CLAHE로 국소 대비를 끌어올려 도드라지게 함."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def dark_channel(img: np.ndarray, patch_size: int) -> np.ndarray:
    """He, Sun, Tang, "Single Image Haze Removal Using Dark Channel Prior" (CVPR 2009 / TPAMI 2011)."""
    min_channel = np.min(img, axis=2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (patch_size, patch_size))
    return cv2.erode(min_channel, kernel)


def estimate_atmospheric_light(img: np.ndarray, dark: np.ndarray, top_fraction: float = 0.001) -> np.ndarray:
    h, w = dark.shape
    n_top = max(int(h * w * top_fraction), 1)
    dark_flat = dark.reshape(-1)
    img_flat = img.reshape(-1, 3)
    brightest_idx = np.argpartition(dark_flat, -n_top)[-n_top:]
    return np.max(img_flat[brightest_idx], axis=0)


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """He, Sun, Tang, "Guided Image Filtering" (ECCV 2010) — DCP 논문 저자들이 투과율 맵의
    블록 노이즈를 없애는 edge-preserving 후처리로 함께 제안한 기법. box filter만으로 구현 가능."""
    guide = guide.astype(np.float64)
    src = src.astype(np.float64)
    mean_guide = cv2.boxFilter(guide, -1, (radius, radius))
    mean_src = cv2.boxFilter(src, -1, (radius, radius))
    mean_guide_src = cv2.boxFilter(guide * src, -1, (radius, radius))
    cov_guide_src = mean_guide_src - mean_guide * mean_src
    mean_guide_sq = cv2.boxFilter(guide * guide, -1, (radius, radius))
    var_guide = mean_guide_sq - mean_guide * mean_guide
    a = cov_guide_src / (var_guide + eps)
    b = mean_src - a * mean_guide
    mean_a = cv2.boxFilter(a, -1, (radius, radius))
    mean_b = cv2.boxFilter(b, -1, (radius, radius))
    return mean_a * guide + mean_b


def smoke_density_map(frame: cv2.typing.MatLike, patch_size: int, omega: float) -> cv2.typing.MatLike:
    """DCP의 투과율 t(x)는 '빛이 안개/연기를 얼마나 뚫고 왔는지'라, 1-t(x)가 곧 연기 밀도 추정치가 됨 —
    이 밀도 맵을 히트맵으로 그리면 연기가 짙은 영역이 도드라져 보임(원래는 반대로 안개 제거에 쓰이는 맵).
    raw dark channel은 블록 아트팩트가 심해 guided filter로 다듬은 뒤 시각화함."""
    img = frame.astype(np.float64) / 255.0
    dark = dark_channel(img, patch_size)
    atmospheric_light = np.clip(estimate_atmospheric_light(img, dark), 1e-3, 1.0)
    normalized = img / atmospheric_light
    raw_transmission = 1.0 - omega * dark_channel(normalized, patch_size)
    gray_guide = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
    transmission = guided_filter(gray_guide, raw_transmission, radius=40, eps=1e-3)
    density = np.clip(1.0 - transmission, 0.0, 1.0)
    density_u8 = (density * 255).astype(np.uint8)
    return cv2.applyColorMap(density_u8, cv2.COLORMAP_JET)


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
    p.add_argument("--set", required=True, choices=["A", "B"], help="A=평상시(정상, label 0) · B=연기 발생(label 1)")
    p.add_argument("--duration-min", type=float, default=7.0, help="수집 시간(분), 기본 7분 (5~10분 권장)")
    p.add_argument("--interval-sec", type=float, default=0.5, help="프레임 저장 간격(초), 기본 0.5초(초당 2장)")
    p.add_argument("--camera-index", type=int, default=1, help="웹캠 장치 인덱스 (기본 1, 엉뚱한 카메라가 열리면 0이나 2 시도)")
    p.add_argument("--out-dir", default="dataset", help="출력 루트 폴더 (기본 dataset/, 세트별 하위폴더 자동 생성)")
    p.add_argument("--no-preview", action="store_true", help="미리보기 창 없이 조용히 수집 (q로 중단 불가, Ctrl+C만 가능)")
    p.add_argument("--preview-scale", type=float, default=0.7,
                   help="미리보기 창 크기 배율 (기본 0.7). 저장되는 이미지 크기와는 무관")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.interval_sec <= 0:
        raise SystemExit("--interval-sec는 0보다 커야 합니다")
    if args.duration_min <= 0:
        raise SystemExit("--duration-min은 0보다 커야 합니다")
    if args.preview_scale <= 0:
        raise SystemExit("--preview-scale은 0보다 커야 합니다")

    out_dir = Path(args.out_dir) / SET_FOLDER[args.set]
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[collect_images] 웹캠(index={args.camera_index}) 여는 중...")
    cap = open_camera(args.camera_index)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

    duration_sec = args.duration_min * 60
    window = f"collect_images - set {args.set} (q=quit)"
    print(f"[collect_images] 세트 {SET_DESC[args.set]} 수집 시작 -> {out_dir} "
          f"(목표 {args.duration_min}분, {args.interval_sec}초 간격)")
    print("[collect_images] Ctrl+C 또는 미리보기 창에서 q로 언제든 중단 가능 (그때까지 저장된 이미지는 유지됨)")
    print("[collect_images] 's' = 원본/CLAHE대비강조/DCP연기맵 순환 · '+'/'-' = 현재 필터 세기 조절 (저장 이미지는 항상 원본)")

    modes = ["원본", "CLAHE", "DCP연기맵"]
    mode_idx = 0
    clahe_clip = 3.0
    dcp_omega = 0.95
    dcp_patch = 15
    count = 0
    start = time.monotonic()
    next_capture = start
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration_sec:
                break

            ok, frame = cap.read()
            if not ok:
                print("[collect_images] !! 프레임을 못 읽었습니다 (카메라 연결 확인)")
                break

            if not args.no_preview:
                remaining_preview = max(0.0, duration_sec - elapsed)
                mode_name = modes[mode_idx]
                if mode_name == "CLAHE":
                    preview = enhance_smoke_contrast(frame, clahe)
                    param_str = f"clipLimit={clahe_clip:.1f}"
                elif mode_name == "DCP연기맵":
                    preview = smoke_density_map(frame, dcp_patch, dcp_omega)
                    param_str = f"omega={dcp_omega:.2f} patch={dcp_patch}"
                else:
                    preview = frame.copy()
                    param_str = ""
                overlay = f"set {args.set} | saved {count} | {remaining_preview:.0f}s left | {mode_name} {param_str} (s=순환, +/-=세기)"
                cv2.putText(preview, overlay, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                if args.preview_scale != 1.0:
                    preview = cv2.resize(preview, None, fx=args.preview_scale, fy=args.preview_scale)
                cv2.imshow(window, preview)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print("[collect_images] 사용자 중단(q)")
                    break
                if key == ord("s"):
                    mode_idx = (mode_idx + 1) % len(modes)
                    print(f"[collect_images] 필터 모드 -> {modes[mode_idx]}")
                elif key in (ord("+"), ord("=")):
                    if mode_name == "CLAHE":
                        clahe_clip = min(clahe_clip + 0.5, 12.0)
                        clahe.setClipLimit(clahe_clip)
                        print(f"[collect_images] CLAHE clipLimit -> {clahe_clip:.1f}")
                    elif mode_name == "DCP연기맵":
                        dcp_omega = min(dcp_omega + 0.05, 1.0)
                        print(f"[collect_images] DCP omega -> {dcp_omega:.2f}")
                elif key in (ord("-"), ord("_")):
                    if mode_name == "CLAHE":
                        clahe_clip = max(clahe_clip - 0.5, 1.0)
                        clahe.setClipLimit(clahe_clip)
                        print(f"[collect_images] CLAHE clipLimit -> {clahe_clip:.1f}")
                    elif mode_name == "DCP연기맵":
                        dcp_omega = max(dcp_omega - 0.05, 0.3)
                        print(f"[collect_images] DCP omega -> {dcp_omega:.2f}")

            now = time.monotonic()
            if now >= next_capture:
                ts = datetime.now()
                fname = f"{ts:%Y%m%d_%H%M%S}_{ts.microsecond // 1000:03d}.jpg"
                cv2.imwrite(str(out_dir / fname), frame)
                count += 1
                next_capture += args.interval_sec
                if next_capture < now:  # 처리 지연으로 밀렸으면 지금 기준으로 재동기화
                    next_capture = now + args.interval_sec
                remaining = max(0.0, duration_sec - elapsed)
                print(f"[{count}] 저장 -> {fname} 남은시간={remaining:.0f}s")
    except KeyboardInterrupt:
        print("\n[collect_images] 사용자 중단(Ctrl+C)")
    finally:
        cap.release()
        if not args.no_preview:
            cv2.destroyAllWindows()

    print(f"[collect_images] 종료 -> 세트 {args.set} {count}장 수집")
    print(f"[collect_images] 폴더: {out_dir}")


if __name__ == "__main__":
    main()
