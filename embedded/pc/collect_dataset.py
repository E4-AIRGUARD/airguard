#!/usr/bin/env python3
"""collect_dataset.py — 웹캠 프레임 + PMS5003 센서값을 묶어 연기감지 데이터셋으로 수집 (노트북에서 실행)
준비: pip install opencv-python pyserial
사용:
  python pc/collect_dataset.py --label 0 --duration-min 7   (세트 A: 평상시, 연기 없음, 5~10분)
  python pc/collect_dataset.py --label 1 --duration-min 7   (세트 B: 연기 발생, 5~10분)
  python pc/collect_dataset.py --label 0 --port COM7 --camera-index 1

라벨 규칙: 세션 전체를 하나의 값으로 고정한다 — 평상시 세션은 --label 0, 연기 세션은 --label 1.
같은 --out-dir로 두 세션을 순서대로 실행하면 이미지는 같은 images/ 폴더에 쌓이고
CSV(dataset.csv)는 이어쓰기(append)되어 최종적으로 세트 A+B가 합쳐진 단일 CSV 1개가 된다.

카메라: innomaker U20CAM-720p (표준 UVC) — Windows에서 CAP_DSHOW 백엔드로 열고 1280x720/MJPG를 요청한다.
동기화: ESP32(firmware/DATASET_collect/DATASET_collect.ino)가 1초마다 보내는 "pm1_0,pm2_5,pm10" 줄을
        받을 때마다 그 시점에 웹캠 프레임 1장을 같이 캡처해 저장한다 — 즉 초당 1쌍(이미지, 센서값)이 쌓인다.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import serial
import serial.tools.list_ports

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp949 콘솔에서 —/· 깨짐 방지

CSV_HEADER = ["timestamp", "image_filename", "pm1_0", "pm2_5", "pm10", "is_smoke"]


def find_port() -> str:
    ports = list(serial.tools.list_ports.comports())
    usb_ports = [p for p in ports if "USB VID:PID" in (p.hwid or "")]
    if not usb_ports:
        sys.exit(
            "ESP32 USB 시리얼 포트를 찾을 수 없습니다 → 노트북에 ESP32가 연결됐는지 확인하거나 "
            "--port COM7 처럼 직접 지정하세요"
        )
    return usb_ports[0].device


def parse_sensor_line(line: str) -> tuple[int, int, int] | None:
    parts = line.strip().split(",")
    if len(parts) != 3:
        return None
    try:
        a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return a, b, c


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
    p.add_argument("--label", type=int, required=True, choices=[0, 1],
                   help="0=세트 A(평상시, 연기 없음) · 1=세트 B(연기 발생)")
    p.add_argument("--duration-min", type=float, default=7.0, help="수집 시간(분), 기본 7분 (5~10분 권장)")
    p.add_argument("--port", help="ESP32 COM 포트 (생략 시 자동 감지)")
    p.add_argument("--camera-index", type=int, default=0, help="웹캠 장치 인덱스 (기본 0)")
    p.add_argument("--out-dir", default="dataset", help="출력 루트 폴더 (기본 dataset/)")
    p.add_argument("--sensor-timeout-sec", type=float, default=3.0,
                   help="센서 줄 하나를 이 시간 안에 못 받으면 재시도 (기본 3초)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    port = args.port or find_port()

    out_dir = Path(args.out_dir)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "dataset.csv"
    is_new_csv = not csv_path.exists()

    print(f"[collect_dataset] ESP32 {port} 115200 여는 중...")
    ser = serial.Serial(port, 115200, timeout=args.sensor_timeout_sec)
    time.sleep(1.5)  # DTR 토글로 ESP32가 리셋되는 시간
    ser.reset_input_buffer()

    print(f"[collect_dataset] 웹캠(index={args.camera_index}) 여는 중...")
    cap = open_camera(args.camera_index)

    label = args.label
    set_name = "A(정상)" if label == 0 else "B(연기)"
    duration_sec = args.duration_min * 60
    print(f"[collect_dataset] 세트 {set_name} 수집 시작 -> {csv_path} / {images_dir} (목표 {args.duration_min}분)")
    print("[collect_dataset] Ctrl+C로 언제든 중단 가능 (그때까지 수집된 데이터는 유지됨)")

    count = 0
    start = time.monotonic()
    try:
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new_csv:
                writer.writerow(CSV_HEADER)
                f.flush()

            while time.monotonic() - start < duration_sec:
                raw = ser.readline()
                if not raw:
                    continue
                sensor = parse_sensor_line(raw.decode("utf-8", "replace"))
                if sensor is None:
                    continue
                pm1_0, pm2_5, pm10 = sensor

                ok, frame = cap.read()
                if not ok:
                    print("[collect_dataset] !! 프레임 캡처 실패, 이 초는 건너뜀")
                    continue

                ts = datetime.now()
                fname = f"{ts:%Y%m%d_%H%M%S}_{ts.microsecond // 1000:03d}.jpg"
                cv2.imwrite(str(images_dir / fname), frame)

                writer.writerow([
                    ts.isoformat(timespec="milliseconds"),
                    f"images/{fname}",
                    pm1_0, pm2_5, pm10, label,
                ])
                f.flush()
                count += 1
                remaining = max(0.0, duration_sec - (time.monotonic() - start))
                print(f"[{count}] pm1.0={pm1_0} pm2.5={pm2_5} pm10={pm10} label={label} "
                      f"남은시간={remaining:.0f}s")
    except KeyboardInterrupt:
        print("\n[collect_dataset] 사용자 중단")
    finally:
        cap.release()
        ser.close()

    print(f"[collect_dataset] 종료 -> 이번 세션 {count}개 샘플 수집 (세트 {set_name})")
    print(f"[collect_dataset] CSV: {csv_path}")


if __name__ == "__main__":
    main()
