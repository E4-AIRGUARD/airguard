#!/usr/bin/env python3
"""new_fan_t 펌웨어(팬+BME280+PMS5003)에 명령을 보내고 DATA CSV 줄을 파일로 저장.
사용: python measure_fan_sensors.py <fan_cmd> <duration_sec> <out_name>
  예: python measure_fan_sensors.py "F 35 0" 60 intake_weak
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

PORT = "COM7"
BAUD = 115200

HEADER = [
    "pc_timestamp", "elapsed_ms", "duty_in_pct", "rpm_in", "duty_out_pct", "rpm_out",
    "temp_c", "rh_pct", "pressure_hpa", "pm1_0", "pm2_5", "pm10", "bme_err", "pms_stale",
]


def main() -> None:
    fan_cmd = sys.argv[1] if len(sys.argv) > 1 else "F 35 0"
    duration_sec = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    out_name = sys.argv[3] if len(sys.argv) > 3 else "measure"

    out_path = Path(__file__).resolve().parent / "data" / f"{out_name}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2.0)
    ser.reset_input_buffer()

    print(f"[measure] {PORT} 연결, '{fan_cmd}' 전송, {duration_sec:.0f}초 기록 -> {out_path}")
    ser.write((fan_cmd + "\n").encode())

    rows = []
    start = time.monotonic()
    try:
        while time.monotonic() - start < duration_sec:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("DATA,"):
                continue
            fields = line[len("DATA,"):].split(",")
            if len(fields) != len(HEADER) - 1:  # pc_timestamp는 여기서 붙임
                continue
            pc_ts = datetime.now().isoformat(timespec="milliseconds")
            rows.append([pc_ts, *fields])
            print(f"[{len(rows)}] {pc_ts} {line}")
    except KeyboardInterrupt:
        print("\n[measure] 사용자 중단")
    finally:
        ser.write(b"0\n")  # 종료 시 정지
        time.sleep(0.3)
        ser.close()

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

    print(f"[measure] 종료 -> {len(rows)}개 샘플, {out_path}")


if __name__ == "__main__":
    main()
