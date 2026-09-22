#!/usr/bin/env python3
"""part_feature_test.py — 미니블록 부품 인식 가능성 1차 시험 (Innomaker 웹캠)

배경: 부품별 준비 목록 검사를 하기 전에, 후보 부품들이 실제 촬영 거리에서
서로 구분되는 특징(길이 차이·돌기·구멍·외곽선)을 실제로 보여주는지부터
확인해야 한다. 이 스크립트는 그 확인 절차만 다룬다 (완성된 인식/분류
시스템이 아님).

준비: pip install opencv-python
사용:
  python part_feature_test.py                    (기본 카메라 index 1)
  python part_feature_test.py --camera-index 0    (엉뚱한 카메라가 열리면 index 바꿔보기)

절차 (설계 문서 4번 항목과 1:1 대응):
  1) 후보 부품을 서로 떨어뜨려 무광 배경에 놓는다   -> (촬영 전 준비, 코드 밖)
  2) Innomaker 1280x720 원본으로 촬영               -> 'c' 키 (전체 프레임 저장)
  3) 유사 부품 2개를 각각 잘라본다                  -> 'r' 키 (마우스로 ROI 2개 선택)
  4) 구분 특징(길이/돌기/구멍)이 실제로 보이는지 확인 -> 저장된 비교 이미지 + 'e' 키(윤곽선 오버레이)
  5) 화면 중앙/가장자리, 방향을 바꿔도 구분되는지    -> 크롭 시 위치/방향 라벨을 물어봄

화면이 뜨면:
  c = 지금 화면 전체를 캡처해서 captures/full/ 에 저장
  r = ROI 2개를 순서대로 드래그 선택 -> 각각 라벨(부품명/위치/방향) 입력 -> 크롭 2장 + 나란히 비교한 이미지 저장
  e = 윤곽선(Canny) 오버레이 미리보기 on/off — 특징이 뭉개지는지 눈으로 먼저 확인
  q = 종료

자동 연속 캡처 (매번 키 누르기 귀찮을 때):
  python part_feature_test.py --auto-count 30 --auto-interval 4
  4초마다 자동으로 전체 프레임을 캡처해서 30장을 찍는다. 캡처 사이 간격 동안
  부품 배치/각도를 바꿀 시간을 벌 수 있다. 화면에 다음 캡처까지 남은 시간과
  진행 개수가 표시되고, 캡처 순간 삑 소리가 난다. q로 중간에 멈출 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    import winsound
except ImportError:
    winsound = None

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

POSITIONS = {"1": "center", "2": "edge"}
ORIENTATIONS = {"1": "0deg", "2": "90deg", "3": "180deg", "4": "270deg"}


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        sys.exit(f"웹캠(index={index})을 열 수 없습니다 → 다른 --camera-index 시도 또는 장치관리자에서 확인")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    return cap


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera-index", type=int, default=1, help="웹캠 장치 인덱스 (기본 1)")
    p.add_argument("--out-dir", default="captures", help="결과 저장 폴더 (기본 captures)")
    p.add_argument("--auto-count", type=int, default=0, help="자동으로 캡처할 전체 프레임 수 (기본 0=비활성, 수동 모드)")
    p.add_argument("--auto-interval", type=float, default=4.0, help="자동 캡처 간격(초), 배치를 바꿀 시간 (기본 4초)")
    p.add_argument("--no-beep", action="store_true", help="자동 캡처 시 알림음 끄기")
    return p.parse_args()


def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    options = ", ".join(f"{k}={v}" for k, v in choices.items())
    while True:
        raw = input(f"{prompt} ({options}): ").strip()
        if raw in choices:
            return choices[raw]
        print("  잘못된 입력입니다. 다시 입력하세요.")


def ask_label(prompt: str) -> str:
    while True:
        raw = input(prompt).strip()
        if raw:
            return "".join(c if c.isalnum() else "_" for c in raw)
        print("  빈 값은 사용할 수 없습니다.")


def select_and_save_pair(frame: np.ndarray, out_dir: Path) -> None:
    """부품 2개를 순서대로 크롭하고, 라벨/위치/방향을 물어본 뒤 개별 저장 + 나란히 비교 이미지를 저장한다."""
    crops: list[tuple[np.ndarray, str]] = []
    for n in (1, 2):
        print(f"\n[part_feature_test] 부품 {n}번 영역을 마우스로 드래그한 뒤 ENTER (취소는 c 또는 ESC)")
        roi = cv2.selectROI("select part (drag, then ENTER)", frame, showCrosshair=True)
        cv2.destroyWindow("select part (drag, then ENTER)")
        x, y, w, h = roi
        if w == 0 or h == 0:
            print(f"[part_feature_test] 부품 {n}번 선택이 취소되어 이번 캡처를 건너뜁니다.")
            return
        crop = frame[y : y + h, x : x + w].copy()

        name = ask_label(f"부품 {n}번 이름/색상 (예: white_short): ")
        position = ask_choice(f"부품 {n}번 화면 위치", POSITIONS)
        orientation = ask_choice(f"부품 {n}번 방향", ORIENTATIONS)
        label = f"{name}_{position}_{orientation}"
        crops.append((crop, label))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pair_dir = out_dir / "pairs" / timestamp
    pair_dir.mkdir(parents=True, exist_ok=True)

    for crop, label in crops:
        cv2.imwrite(str(pair_dir / f"{label}.jpg"), crop)

    side_by_side = make_side_by_side(crops)
    compare_path = pair_dir / "compare.jpg"
    cv2.imwrite(str(compare_path), side_by_side)
    print(f"[part_feature_test] 저장됨 -> {pair_dir} (crop 2장 + compare.jpg)")


def make_side_by_side(crops: list[tuple[np.ndarray, str]], target_height: int = 300) -> np.ndarray:
    resized = []
    for crop, label in crops:
        h, w = crop.shape[:2]
        scale = target_height / h
        img = cv2.resize(crop, (int(w * scale), target_height))
        cv2.putText(img, label, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        resized.append(img)
    gap = np.full((target_height, 10, 3), 255, dtype=np.uint8)
    return np.hstack([resized[0], gap, resized[1]])


def with_edge_overlay(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 150)
    edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(frame, 0.7, edges_bgr, 0.6, 0)


def save_full_frame(frame: np.ndarray, out_dir: Path) -> Path:
    full_dir = out_dir / "full"
    full_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    path = full_dir / fname
    cv2.imwrite(str(path), frame)
    return path


def draw_countdown(display: np.ndarray, remaining_seconds: float, done: int, total: int) -> None:
    progress_text = f"auto capture {done}/{total}"
    countdown_text = f"next in {max(remaining_seconds, 0):.1f}s"
    cv2.putText(display, progress_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(display, countdown_text, (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2, cv2.LINE_AA)


def beep(enabled: bool) -> None:
    if enabled and winsound is not None:
        winsound.Beep(1000, 120)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    beep_enabled = not args.no_beep

    print(f"[part_feature_test] 웹캠(index={args.camera_index}) 여는 중...")
    cap = open_camera(args.camera_index)
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"[part_feature_test] 해상도 {actual_w:.0f}x{actual_h:.0f}")

    auto_total = max(args.auto_count, 0)
    auto_done = 0
    next_auto_capture = time.time() + args.auto_interval if auto_total > 0 else None
    if auto_total > 0:
        print(f"[part_feature_test] 자동 캡처: {args.auto_interval}초 간격으로 {auto_total}장 (q로 중단 가능)")
    else:
        print("[part_feature_test] c=전체캡처, r=부품 2개 크롭+비교, e=윤곽선 미리보기 on/off, q=종료")

    show_edges = False
    captured = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[part_feature_test] !! 프레임을 못 읽었습니다 (카메라 연결 확인)")
                break

            display = with_edge_overlay(frame) if show_edges else frame

            if next_auto_capture is not None:
                remaining = next_auto_capture - time.time()
                draw_countdown(display, remaining, auto_done, auto_total)
                if remaining <= 0:
                    path = save_full_frame(frame, out_dir)
                    captured += 1
                    auto_done += 1
                    beep(beep_enabled)
                    print(f"[part_feature_test] 자동 캡처 {auto_done}/{auto_total} -> {path}")
                    if auto_done >= auto_total:
                        print("[part_feature_test] 자동 캡처 완료 — 계속 수동으로 찍으려면 c, 끝내려면 q")
                        next_auto_capture = None
                    else:
                        next_auto_capture = time.time() + args.auto_interval

            cv2.imshow("part feature test (c=capture, r=crop pair, e=edges, q=quit)", display)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            elif key == ord("e"):
                show_edges = not show_edges
                print(f"[part_feature_test] 윤곽선 미리보기: {'ON' if show_edges else 'OFF'}")
            elif key == ord("c"):
                path = save_full_frame(frame, out_dir)
                captured += 1
                print(f"[part_feature_test] 전체 프레임 저장됨 -> {path} (총 {captured}장)")
            elif key == ord("r"):
                select_and_save_pair(frame, out_dir)
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"[part_feature_test] 종료 — 전체 캡처 {captured}장")


if __name__ == "__main__":
    main()
