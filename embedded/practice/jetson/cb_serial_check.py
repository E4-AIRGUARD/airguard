#!/usr/bin/env python3
"""cb_serial_check.py — Jetson에서 ESP32(T08) JSON 줄을 받고 명령을 보내는 최소 확인 스크립트
준비:  sudo apt install python3-serial   (또는 pip install pyserial)
       sudo usermod -aG dialout $USER  → 재로그인
사용:  python3 cb_serial_check.py            (포트 자동: /dev/ttyUSB0)
       python3 cb_serial_check.py /dev/ttyUSB1
       실행 중 입력:  C 20 20   /  T 600  /  T -1  /  E supply_start  /  q
       (2026-09-28: 가열패드 미배선으로 C 명령에서 ht 인자 제거 — "C fi fo"만 받음)
"""
import sys, json, time, glob, threading
import serial

port = sys.argv[1] if len(sys.argv) > 1 else (glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + [None])[0]
if not port:
    sys.exit("포트 없음 → lsusb에 'Silicon Labs CP210x'가 보이는지, ls -l /dev/ttyUSB* 확인")
print(f"[jetson] open {port} 115200")
ser = serial.Serial(port, 115200, timeout=1)
time.sleep(1.5)                      # DTR 토글로 ESP32가 리셋되는 시간

def reader():
    ok = bad = 0
    while True:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        try:
            d = json.loads(line); ok += 1
        except json.JSONDecodeError:
            bad += 1; print(f"[raw] {line}"); continue
        ts = time.strftime("%H:%M:%S")
        if d.get("k") == "sens":
            print(f"{ts} SENS seq={d['seq']} T={d['t']:.2f} RH={d['rh']:.1f} PM2.5={d['pm25']} err={d['err']}")
        elif d.get("k") == "stat":
            print(f"{ts} STAT {d['st']:8s} fan={d['fi']}/{d['fo']} rpm={d['rpmIn']}/{d['rpmOut']} test={d['test']}   (json ok={ok} bad={bad})")
        else:
            print(f"{ts} {d}")

threading.Thread(target=reader, daemon=True).start()
try:
    while True:
        cmd = input().strip()
        if cmd == "q":
            break
        if cmd:
            ser.write((cmd + "\n").encode())
finally:
    ser.close()
