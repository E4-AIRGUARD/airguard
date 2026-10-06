#!/usr/bin/env python3
"""log_to_csv.py — PC(VSCode)에서 ESP32의 DATA_log.ino CSV 줄을 받아 파일로 저장
준비:  pip install pyserial
사용:  python pc/log_to_csv.py                              (포트 자동 감지, 무제한 기록)
       python pc/log_to_csv.py --port COM7
       python pc/log_to_csv.py --out logs/오늘측정.csv
       python pc/log_to_csv.py --count 1000                 (정확히 1000개 받으면 자동 종료)
       python pc/log_to_csv.py --fan-step-sec 120            (각 단계 유지 시간 조정)
       python pc/log_to_csv.py --no-fan-auto                (팬 자동 순환 끄기 — 수동 제어만)
       python pc/log_to_csv.py --device-id CleanBox-01
       python pc/log_to_csv.py --preheat-sec 5                (측정 시작 전 5초 대기, 드라이기 등으로 미리 가열)

팬 속도: 실기 테스트로 이 팬은 15%·22%에서는 기동도 유지도 안 되고(배기 분리 상태에서도 재현),
         30% 이상에서만 동작하며 60/100%와도 체감 속도 차이가 없음을 확인함 — 즉 이 하드웨어로는
         그라데이션 속도 조절이 불가능하고 사실상 꺼짐/켜짐(30%) 두 상태만 존재함.
         그래서 약/중/강 3단계를 포기하고 아래 3가지 조건만 자동 순환(각 --fan-step-sec 초, 기본 120초):
         무팬(0/0) -> 흡기ON(30/0) -> 흡기+배기 동시ON(30/30) -> (반복)
         배기팬은 DFR0457 병렬 연결이 실기에서 반응 없어(배선 확인해도 재현) 5V 직결로 전환 —
         속도 조절 불가, ON/OFF만 가능. "동시" 단계일 때만 배기 VCC를 5V에 꽂고,
         그 외(무팬/흡기단독) 단계에선 물리적으로 뽑아둘 것. exhaust_fan_pwm 값(30)은
         실제 속도가 아니라 "이 구간엔 배기를 꽂아야 함"을 알려주는 라벨일 뿐.
         --fan-steps "in/out,in/out,..." 로 직접 지정 가능, --no-fan-auto 로 끄면 수동 제어로 전환.

단축키(엔터 없이 바로): s = 로깅 일시정지 · r = 로깅 재개 · q = 종료
(msvcrt 없는 환경이면 자동으로 줄 단위 입력 방식으로 폴백)

출력 컬럼: timestamp, device_id, elapsed_ms, inside_temp_c, inside_rh_pct,
           outside_temp_c_baseline, outside_rh_pct_baseline,
           target_temp_c, target_rh_pct, environment_state, fan_mode,
           intake_fan_pwm/intake_fan_rpm, exhaust_fan_pwm/exhaust_fan_rpm,
           smoke, pm25_ugm3
ESP32엔 실시간 시계가 없어 elapsed_ms만 보내므로, 이 스크립트가 줄을 받는 시점의
PC 시스템 시각(timestamp)을 붙여서 저장한다. outside_*_baseline은 별도 외부 센서값이
아니라, DATA_log가 부팅 직후 팬 0 % 상태에서 고정한 BME280 기준값이다.
"""
from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports

try:
    import msvcrt
except ImportError:
    msvcrt = None  # Windows 외 환경 폴백

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp949 콘솔에서 —/· 깨짐 방지

# ESP32(DATA_log.ino)가 실제로 보내는 원본 CSV 필드 순서
RAW_FIELDS = [
    "elapsed_ms", "inside_temp_c", "inside_rh_pct",
    "outside_temp_c_baseline", "outside_rh_pct_baseline",
    "target_temp_c", "target_rh_pct", "environment_state", "fan_mode",
    "intake_fan_pwm", "intake_fan_rpm",
    "exhaust_fan_pwm", "exhaust_fan_rpm", "smoke", "pm25_ugm3",
]
OUT_HEADER = ["timestamp", "device_id", *RAW_FIELDS]

DEFAULT_DEVICE_ID = "ESP32_38-3E-51-C0-EC-FC"
# 15%·22%는 기동/유지 둘 다 실패(배기 분리 상태에서도 재현) -> 그라데이션 속도 조절 불가로 결론.
# 30% 이상만 동작하고 그 이상은 속도 차이 없음 -> 무팬 / 흡기ON / 흡기+배기 동시ON 3조건으로 단순화.
FAN_ON = 30
DEFAULT_FAN_STEPS: list[tuple[int, int]] = [
    (0, 0),
    (FAN_ON, 0),
    (FAN_ON, FAN_ON),
]
DEFAULT_FAN_STEP_SEC = 120


def fan_label(fan_in: int, fan_out: int) -> str:
    if fan_in == 0 and fan_out == 0:
        return "off"
    if fan_out == 0:
        return f"in{fan_in}"
    return f"in{fan_in}_out{fan_out}"


def parse_fan_steps(spec: str) -> list[tuple[int, int]]:
    steps = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            a, b = part.split("/", 1)
            steps.append((int(a), int(b)))
        else:
            v = int(part)
            steps.append((v, v))
    if not steps:
        raise ValueError("팬 단계가 비어 있습니다. 예: 0/0,30/0,30/30")
    for fan_in, fan_out in steps:
        if not 0 <= fan_in <= 100 or not 0 <= fan_out <= 100:
            raise ValueError("팬 PWM은 0~100 범위여야 합니다")
    return steps


def parse_data_line(line: str) -> dict[str, str] | None:
    """DATA_log의 한 CSV 행을 필드명-값 매핑으로 변환하고 숫자 형식을 검증한다."""
    try:
        fields = next(csv.reader([line]))
    except csv.Error:
        return None
    if len(fields) != len(RAW_FIELDS):
        return None

    record = dict(zip(RAW_FIELDS, fields))
    try:
        int(record["elapsed_ms"])
        for name in (
            "inside_temp_c", "inside_rh_pct",
            "outside_temp_c_baseline", "outside_rh_pct_baseline",
            "target_temp_c", "target_rh_pct",
        ):
            float(record[name])
        for name in (
            "intake_fan_pwm", "intake_fan_rpm",
            "exhaust_fan_pwm", "exhaust_fan_rpm", "pm25_ugm3",
        ):
            int(record[name])
    except ValueError:
        return None
    return record


def find_port() -> str:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        sys.exit("포트 없음 → 장치관리자에서 ESP32(CP210x/CH9102) COM 포트 확인")
    usb_ports = [p for p in ports if "USB VID:PID" in (p.hwid or "")]
    if not usb_ports:
        sys.exit(
            "USB 시리얼 포트 없음(블루투스 COM만 보임) → ESP32 USB 연결 확인, "
            "필요하면 포트를 직접 지정: python pc/log_to_csv.py --port COM7"
        )
    return usb_ports[0].device


def open_output(path: str | None) -> Path:
    out = Path(path) if path else Path("logs") / f"{datetime.now():%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def reader_loop(
    ser: serial.Serial,
    out_path: Path,
    stop: threading.Event,
    count: int | None,
    device_id: str,
) -> None:
    ok = bad = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUT_HEADER)
        f.flush()

        while not stop.is_set():
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue

            record = parse_data_line(line)
            if record is not None:
                ts = datetime.now().isoformat(timespec="milliseconds")
                writer.writerow([ts, device_id, *(record[name] for name in RAW_FIELDS)])
                f.flush()
                ok += 1
                print(
                    f"{ts} T={record['inside_temp_c']} RH={record['inside_rh_pct']} "
                    f"state={record['environment_state']} "
                    f"fan={record['fan_mode']} "
                    f"rpm={record['intake_fan_rpm']}/{record['exhaust_fan_rpm']} "
                    f"({ok}{f'/{count}' if count else ''} bad={bad})"
                )
                if count is not None and ok >= count:
                    print(f"[log_to_csv] 목표 {count}개 도달 -> 자동 종료")
                    stop.set()
            elif line.startswith("elapsed_ms,"):
                print("[log_to_csv] ESP32 CSV 헤더 확인")
            else:
                bad += 1
                print(f"[raw] {line}")


def fan_scheduler_loop(
    ser: serial.Serial, stop: threading.Event, steps: list[tuple[int, int]], step_sec: int
) -> None:
    i = 0
    while not stop.is_set():
        fan_in, fan_out = steps[i % len(steps)]
        ser.write(f"C {fan_in} {fan_out}\n".encode())
        print(f"[log_to_csv] fan auto -> {fan_label(fan_in, fan_out)}")
        i += 1
        for _ in range(step_sec):
            if stop.is_set():
                return
            time.sleep(1)


def keypress_loop(ser: serial.Serial, stop: threading.Event) -> None:
    """s=일시정지 r=재개 q=종료. msvcrt 있으면 엔터 없이 즉시 반응, 없으면 줄 단위로 폴백."""
    if msvcrt is None:
        while not stop.is_set():
            try:
                cmd = input().strip().lower()
            except EOFError:
                return
            except KeyboardInterrupt:
                stop.set()
                return
            if cmd == "q":
                stop.set()
                return
            if cmd == "s":
                ser.write(b"STOP\n")
                print("[log_to_csv] 로깅 일시정지")
            elif cmd == "r":
                ser.write(b"START\n")
                print("[log_to_csv] 로깅 재개")
        return

    while not stop.is_set():
        if msvcrt.kbhit():
            ch = msvcrt.getch().decode(errors="ignore").lower()
            if ch == "q":
                stop.set()
                return
            elif ch == "s":
                ser.write(b"STOP\n")
                print("[log_to_csv] 로깅 일시정지 (s)")
            elif ch == "r":
                ser.write(b"START\n")
                print("[log_to_csv] 로깅 재개 (r)")
        else:
            time.sleep(0.1)


def parse_args() -> argparse.Namespace:
    default_steps_str = ",".join(f"{a}/{b}" for a, b in DEFAULT_FAN_STEPS)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="COM 포트 (생략 시 자동 감지)")
    p.add_argument("--out", help="출력 CSV 경로 (생략 시 logs/YYYYmmdd_HHMMSS.csv)")
    p.add_argument("--count", type=int, default=None, help="이 개수만큼 받으면 자동 종료 (생략 시 무제한, q로 수동 종료)")
    p.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help=f"CSV device_id 값 (기본 {DEFAULT_DEVICE_ID})")
    p.add_argument("--fan-steps", default=default_steps_str, help="쉼표구분 '흡기/배기' 순환 단계 (기본: 무팬,흡기30,배기30,흡기+배기30)")
    p.add_argument("--fan-step-sec", type=int, default=DEFAULT_FAN_STEP_SEC, help="각 단계 유지 시간(초) (기본 120)")
    p.add_argument("--no-fan-auto", action="store_true", help="팬 자동 순환을 끄고 수동 제어만 사용")
    p.add_argument("--fan-in", type=int, default=0, help="--no-fan-auto일 때 시작 시 고정할 흡기 팬 %% (기본 0=무팬)")
    p.add_argument("--fan-out", type=int, default=0, help="--no-fan-auto일 때 시작 시 고정할 배기 팬 %% (기본 0=무팬)")
    p.add_argument("--preheat-sec", type=int, default=0,
                   help="측정 시작 전 대기 시간(초) — 이 동안 드라이기 등으로 미리 가열 가능, 팬/로깅은 대기 후 시작 (기본 0=바로 시작)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    port = args.port or find_port()
    out_path = open_output(args.out)
    try:
        fan_steps = parse_fan_steps(args.fan_steps)
    except ValueError as exc:
        raise SystemExit(f"--fan-steps 오류: {exc}") from exc
    if args.count is not None and args.count <= 0:
        raise SystemExit("--count는 1 이상이어야 합니다")
    if args.fan_step_sec <= 0:
        raise SystemExit("--fan-step-sec는 1 이상이어야 합니다")
    if not 0 <= args.fan_in <= 100 or not 0 <= args.fan_out <= 100:
        raise SystemExit("--fan-in/--fan-out은 0~100 범위여야 합니다")
    if args.preheat_sec < 0:
        raise SystemExit("--preheat-sec는 0 이상이어야 합니다")

    print(f"[log_to_csv] open {port} 115200 -> {out_path}")
    if args.count:
        print(f"[log_to_csv] 목표 {args.count}개 (약 {args.count}초 소요 예상, 1초당 1개 기준)")
    if not args.no_fan_auto:
        labels = [fan_label(a, b) for a, b in fan_steps]
        print(f"[log_to_csv] 팬 자동 순환: {labels} 각 {args.fan_step_sec}초")
    print("[log_to_csv] 단축키 — s: 일시정지 · r: 재개 · q: 종료")

    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(1.5)  # DTR 토글로 ESP32가 리셋되는 시간
    ser.reset_input_buffer()

    if args.preheat_sec > 0:
        print(f"[log_to_csv] 사전 가열 {args.preheat_sec}초 — 지금 드라이기 등으로 내부 온도를 올려주세요 (팬/로깅은 아직 시작 안 함)")
        for remaining in range(args.preheat_sec, 0, -1):
            print(f"[log_to_csv]   {remaining}...")
            time.sleep(1)
        print("[log_to_csv] 사전 가열 종료 -> 팬/로깅 시작")

    if args.no_fan_auto:
        ser.write(f"C {args.fan_in} {args.fan_out}\n".encode())
        print(f"[log_to_csv] 팬 고정: {fan_label(args.fan_in, args.fan_out)} (자동 순환 없음)")

    stop = threading.Event()
    threads = [
        threading.Thread(
            target=reader_loop, args=(ser, out_path, stop, args.count, args.device_id), daemon=True
        ),
        threading.Thread(target=keypress_loop, args=(ser, stop), daemon=True),
    ]
    if not args.no_fan_auto:
        threads.append(
            threading.Thread(
                target=fan_scheduler_loop, args=(ser, stop, fan_steps, args.fan_step_sec), daemon=True
            )
        )
    for t in threads:
        t.start()

    stop.wait()
    ser.close()
    print(f"\n[log_to_csv] 종료 -> {out_path}")


if __name__ == "__main__":
    main()
