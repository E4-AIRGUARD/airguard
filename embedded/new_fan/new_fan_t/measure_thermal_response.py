#!/usr/bin/env python3
"""measure_thermal_response.py — 음압 제어 열 회복 실험 (드라이기 가열 → 흡기+배기 동시 냉각) 측정.

기본 thermal 모드의 핵심 규칙: SSOT의 흡기35/배기60, 흡기60/배기100 조합만 허용한다(정지 0/0은 예외). 이
스크립트는 흡기단독/배기단독 조합을 만들 수 없다 — 항상 흡기+배기 동시(F <in> <out>)만 보낸다.
별도 --experiment clean-air 모드는 가열·연기 없는 3×3 조합을 허용하고 Heat_Load=0으로 고정한다.
전체 9조합 실행은 measure_clean_air_matrix.py를 사용한다.
카메라 1번에서 기본 1초 간격 JPG를 별도 폴더에 저장한다(--no-camera로 생략 가능).

최종 CSV 스키마(요청대로 고정): Time, Temp, Humidity, Exhaust_PWM, Intake_PWM, Heat_Load,
Exhaust_RPM, Intake_RPM, PM2.5

Heat_Load(0/1)는 드라이기가 수동 장치라 펌웨어가 알 방법이 없다 — 측정 중 키보드로 사람이
표시한다: h=드라이기 ON(Heat_Load=1) · c=드라이기 OFF(Heat_Load=0) · q=이 구간 조기 종료.

팬 조합은 약풍(35)·중풍(60)·강풍(100) 세 단계 중 SSOT에서 정한
(흡기35,배기60)과 (흡기60,배기100)만 사용한다. Phase1은 앞쪽 조합을,
Phase2·3은 뒤쪽 조합을 쓴다. 이번 회차의 Phase1~3은 5초 가열하므로 Phase3은 반복 측정이다.

가열 목표온도: 5초 가열로는 39도 정도까지만 올라가는 게 실측 확인돼서, 예시는 45도 대신
38도를 예시 목표값으로 둔다. 5초 가열 후에는 사람이 드라이기를 끄고 c를 누른다.

문 개방 외란 테스트는 이번 회차에서 물리적으로 불가능해서 Phase4 예시에서 뺐다 —
지속 가열 + 팬 평형 유지까지만 진행한다.

사용 (베이스라인 + 4개 Phase, 순서대로 한 번씩 실행):
  Phase 0 (베이스라인, 팬·가열 없음):
    python measure_thermal_response.py phase0_baseline --duration-min 5 --intake 0 --exhaust 0
  Phase 1 (저속 냉각 — 흡기약풍35/배기중풍60):
    python measure_thermal_response.py phase1_low --duration-min 15 --intake 35 --exhaust 60 --heat-until-temp 38
  Phase 2 (중속 냉각 — 흡기중풍60/배기강풍100):
    python measure_thermal_response.py phase2_mid --duration-min 15 --intake 60 --exhaust 100 --heat-until-temp 38
  Phase 3 (조합 B 반복 — Phase2와 같은 5초 가열, 측정 시간 10분):
    python measure_thermal_response.py phase3_high --duration-min 10 --intake 60 --exhaust 100 --heat-until-temp 38
  Phase 4 (지속 가열 + 팬 평형 유지 — 흡기중풍60/배기강풍100):
    python measure_thermal_response.py phase4_sustained --duration-min 15 --intake 60 --exhaust 100 --heat-from-start

--heat-until-temp 모드: 시작 시 팬은 꺼진 채(Heat_Load는 h 키로 사람이 1로 표시) 온도가 그
값에 도달할 때까지 기다렸다가, 도달 즉시 Heat_Load=0으로 자동 전환하고 지정한 흡기/배기
duty를 바로 적용한다. 드라이기는 자동으로 꺼지지 않으므로 도달 알림을 보고 사람이 끈다.
--heat-from-start 모드: 처음부터 지정한 흡기/배기 duty로 팬을 돌리고, Heat_Load는 계속
사람이 키보드로 표시(phase4는 15분 내내 ON으로 두면 됨).
둘 다 안 주면: 처음부터 지정한 duty로 팬만 돌리고 Heat_Load=0으로 시작(phase0용).
모든 모드는 시작 전에 같은 정상 DATA에서 PM2.5 17~20 및 온도 22~26°C를 확인한다.
조건 충족 전에는 CSV/JPG를 저장하지 않으며 대기 시간은 측정 시간에 포함하지 않는다.
시작 후의 온도·PM 상승은 실험값으로 계속 기록한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

from thermal_camera import CameraRecorder

try:
    import msvcrt
except ImportError:
    msvcrt = None  # Windows 외 환경에서는 실시간 키 입력 미지원

PORT = "COM7"
BAUD = 115200
DATA_DIR = Path(__file__).resolve().parent / "data" / "thermal_response"
IMAGE_DIR = Path(__file__).resolve().parent / "data" / "thermal_images"
ALLOWED_FAN_PAIRS = {(0, 0), (35, 60), (60, 100)}
START_PM25_MIN, START_PM25_MAX = 17, 20
START_TEMP_MIN, START_TEMP_MAX = 22.0, 26.0
CLEAN_AIR_FAN_PAIRS = {(i, o) for i in (35, 60, 100) for o in (35, 60, 100)}

CSV_HEADER = ["Time", "Temp", "Humidity", "Exhaust_PWM", "Intake_PWM", "Heat_Load",
              "Exhaust_RPM", "Intake_RPM", "PM2.5"]


def negative_pressure_ok(intake: int, exhaust: int) -> bool:
    """SSOT에 지정된 실험 조합만 허용한다. 실제 차압을 판정하는 함수는 아니다."""
    return (intake, exhaust) in ALLOWED_FAN_PAIRS


def parse_data_line(line: str) -> dict | None:
    if not line.startswith("DATA,"):
        return None
    fields = line[len("DATA,"):].split(",")
    if len(fields) != 13:  # elapsed_ms,duty_in,rpm_in,duty_out,rpm_out,temp,rh,p,pm1,pm25,pm10,err,stale
        return None
    try:
        return {
            "duty_in": int(fields[1]), "rpm_in": int(fields[2]),
            "duty_out": int(fields[3]), "rpm_out": int(fields[4]),
            "temp_c": float(fields[5]), "rh_pct": float(fields[6]),
            "pm2_5": int(fields[9]), "bme_err": int(fields[11]),
            "pms_stale": int(fields[12]),
        }
    except ValueError:
        return None


def parse_event(spec: str) -> tuple[float, str]:
    if ":" not in spec:
        raise ValueError(f"--event-at-min 형식 오류(분:메시지): {spec}")
    minute_str, msg = spec.split(":", 1)
    minute = float(minute_str)
    if not math.isfinite(minute) or minute < 0 or not msg.strip():
        raise ValueError("이벤트는 0 이상의 유한한 분 값과 메시지가 필요합니다")
    return minute, msg.strip()


class HeatKeyReader:
    """Windows 콘솔에서 h/c 키로 Heat_Load를 실시간 토글."""

    def __init__(self, allow_heat: bool = True) -> None:
        self.allow_heat = allow_heat
        self.heat_load = 0
        self.quit = False

    def poll(self) -> None:
        if msvcrt is None:
            return
        while msvcrt.kbhit():
            ch = msvcrt.getch().decode("utf-8", "ignore").lower()
            if ch == "h" and self.allow_heat:
                self.heat_load = 1
                print("[thermal] Heat_Load = 1 (드라이기 ON 표시)")
            elif ch == "c":
                self.heat_load = 0
                print("[thermal] Heat_Load = 0 (드라이기 OFF 표시)")
            elif ch == "q":
                self.quit = True
                print("[thermal] 조기 종료 요청(q)")
                break



class StartCheckCancelled(Exception):
    """시작 조건 대기 중 사용자가 취소함."""


def start_conditions_ok(sample: dict | None) -> bool:
    """같은 DATA 샘플의 온도·PM2.5와 센서 상태로 시작 가능 여부를 판단한다."""
    return bool(sample is not None
                and sample["bme_err"] == 0 and sample["pms_stale"] == 0
                and math.isfinite(sample["temp_c"]) and math.isfinite(sample["pm2_5"])
                and START_TEMP_MIN <= sample["temp_c"] <= START_TEMP_MAX
                and START_PM25_MIN <= sample["pm2_5"] <= START_PM25_MAX)


def wait_for_start_conditions(ser, args, camera=None) -> dict | None:
    """기록·촬영·팬 명령 전에 대기한다. None은 사용자가 취소했다는 뜻이다."""
    print("[start-check] PM2.5 17~20, 온도 22~26°C 확인 중. "
          "조건 충족 전에는 CSV·사진을 기록하지 않습니다. q=취소")
    waiting_keys = HeatKeyReader(allow_heat=False)
    started = time.monotonic()
    next_status = started

    def cancelled():
        waiting_keys.poll()
        return waiting_keys.quit or (args.stop_file is not None and args.stop_file.exists())

    while True:
        if cancelled():
            return None
        if camera is not None:
            camera.check()
        raw = ser.readline()
        if cancelled():
            return None
        if camera is not None:
            camera.check()
        sample = parse_data_line(raw.decode("utf-8", "replace").strip()) if raw else None
        if start_conditions_ok(sample):
            check = {"checked_at": datetime.now().isoformat(timespec="milliseconds"),
                     "temp_c": sample["temp_c"], "pm2_5": sample["pm2_5"],
                     "bme_err": sample["bme_err"], "pms_stale": sample["pms_stale"],
                     "wait_seconds": round(time.monotonic() - started, 3)}
            print(f"[start-check] 통과: T={sample['temp_c']:.2f}°C, PM2.5={sample['pm2_5']}. "
                  "설정한 팬 조건으로 센서 기록·촬영을 시작합니다.")
            return check
        now = time.monotonic()
        if now >= next_status:
            if sample is None:
                print("[start-check] 유효한 DATA 수신 대기 중 (기록·촬영 전)")
            else:
                print(f"[start-check] 대기: T={sample['temp_c']:.2f}°C, PM2.5={sample['pm2_5']}, "
                      f"BME오류={sample['bme_err']}, PMS갱신지연={sample['pms_stale']}")
            next_status = now + 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment", choices=("thermal", "clean-air"), default="thermal",
                   help="thermal: 열 회복 / clean-air: 가열·연기 없는 팬 조합 측정")
    p.add_argument("phase_name", help="출력 파일/폴더 이름 (예: phase1_low)")
    p.add_argument("--duration-min", type=float, required=True, help="이 구간 측정 시간(분)")
    p.add_argument("--intake", type=int, required=True, help="흡기 duty %% (냉각 단계 목표값)")
    p.add_argument("--exhaust", type=int, required=True, help="배기 duty %% (냉각 단계 목표값, 흡기보다 20%%p 이상 높아야 함)")
    p.add_argument("--heat-until-temp", type=float, default=None,
                    help="이 온도(°C) 도달 전까지는 팬 꺼둔 채 대기, 도달 즉시 Heat_Load=0 + 팬 duty 적용")
    p.add_argument("--heat-from-start", action="store_true",
                    help="처음부터 지정한 duty로 팬 가동(Heat_Load는 키보드로 계속 사람이 표시)")
    p.add_argument("--event-at-min", action="append", default=[], type=parse_event,
                    metavar="분:메시지", help="경과 N분 시점에 콘솔에 알림(반복 가능) — 예: '5:중간 상태 확인'")
    p.add_argument("--out-dir", default=None, help="출력 폴더 (기본: data/thermal_response/<phase_name>/)")
    p.add_argument("--port", default=PORT, help=f"시리얼 포트 (기본: {PORT})")
    p.add_argument("--camera-index", type=int, default=1, help="카메라 인덱스 (기본: 1)")
    p.add_argument("--image-interval-sec", type=float, default=1.0, help="사진 저장 간격(초, 기본: 1)")
    p.add_argument("--image-dir", type=Path, default=IMAGE_DIR, help="사진 저장 루트 폴더")
    p.add_argument("--stop-file", type=Path, default=None, help="이 파일이 생기면 측정을 중단")
    p.add_argument("--no-camera", action="store_true", help="카메라 없이 센서만 측정")
    args = p.parse_args(argv)
    if args.camera_index < 0:
        p.error("--camera-index는 0 이상이어야 합니다")
    if not math.isfinite(args.image_interval_sec) or args.image_interval_sec <= 0:
        p.error("--image-interval-sec은 0보다 큰 유한한 값이어야 합니다")
    if not args.phase_name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.phase_name):
        p.error("phase_name은 영문, 숫자, 밑줄, 하이픈만 사용할 수 있습니다")
    if not math.isfinite(args.duration_min) or args.duration_min <= 0:
        p.error("--duration-min은 0보다 큰 유한한 값이어야 합니다")
    if args.experiment == "clean-air":
        if (args.intake, args.exhaust) not in CLEAN_AIR_FAN_PAIRS:
            p.error("clean-air는 흡기·배기 각각 35/60/100만 허용합니다")
        if args.heat_until_temp is not None or args.heat_from_start:
            p.error("clean-air에서는 가열 옵션을 사용할 수 없습니다")
    elif not negative_pressure_ok(args.intake, args.exhaust):
        p.error("허용 팬 조합(흡기/배기): 0/0, 35/60, 60/100. 단독·동일속도·임의 PWM은 금지합니다")
    if args.heat_until_temp is not None:
        if not math.isfinite(args.heat_until_temp):
            p.error("--heat-until-temp는 유한한 값이어야 합니다")
        if args.heat_from_start:
            p.error("--heat-until-temp 와 --heat-from-start는 동시에 쓸 수 없습니다")
    if (args.heat_until_temp is not None or args.heat_from_start) and args.intake == 0:
        p.error("가열 모드에는 흡기35/배기60 또는 흡기60/배기100을 지정하세요")
    return args


def main(argv: list[str] | None = None) -> bool:
    # Windows 파이프의 cp949에서도 한글·기호 출력으로 측정이 중단되지 않도록 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.stop_file is not None and args.stop_file.exists():
        return False
    out_dir = Path(args.out_dir) if args.out_dir else DATA_DIR / args.phase_name
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{args.phase_name}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    out_path = out_dir / f"{run_id}.csv"
    image_path = args.image_dir / args.phase_name / run_id

    duration_sec = args.duration_min * 60
    pending_events = sorted(args.event_at_min, key=lambda x: x[0])

    fans_active = args.heat_until_temp is None  # heat-until-temp 모드는 처음엔 팬 꺼둠
    keys = HeatKeyReader(allow_heat=args.experiment == "thermal")
    if msvcrt is None:
        print("[thermal] !! msvcrt 없음 — 이 환경에선 h/c 실시간 토글이 안 됩니다. Heat_Load는 계속 "
              f"{keys.heat_load}으로 고정됩니다.")

    if args.experiment == "clean-air":
        print("[clean-air] 가열·연기 없음. Heat_Load=0 고정. 키: q=측정 종료")
    else:
        print("[thermal] 키: h=Heat_Load 1 · c=Heat_Load 0 · q=조기종료")

    row_count = 0
    ser = None
    camera = None
    failure = None
    interrupted = False
    try:
        if not args.no_camera:
            camera = CameraRecorder(image_path, args.camera_index, args.image_interval_sec)
            camera.open()
            (image_path / "run.json").write_text(json.dumps({
                "phase": args.phase_name, "run_id": run_id, "sensor_csv": str(out_path.resolve()),
                "experiment": args.experiment, "intake": args.intake, "exhaust": args.exhaust,
                "camera_index": args.camera_index, "image_interval_sec": args.image_interval_sec,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[thermal] 카메라 준비 완료: {image_path} (약 {args.image_interval_sec:g}초 간격)")
        ser = serial.Serial(args.port, BAUD, timeout=1, write_timeout=1)
        time.sleep(2.0)
        ser.reset_input_buffer()
        start_check = wait_for_start_conditions(ser, args, camera)
        if start_check is None:
            raise StartCheckCancelled
        # CSV 컬럼을 유지하면서 별도 JSON에 시작 기준 충족 근거를 남긴다.
        out_path.with_suffix(".start_check.json").write_text(
            json.dumps(start_check, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.heat_from_start:
            keys.heat_load = 1
            print("[thermal] --heat-from-start: Heat_Load=1로 시작 (필요시 c로 꺼주세요)")
        with out_path.open("x", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            f.flush()
            if fans_active:
                cmd = f"F {args.intake} {args.exhaust}"
                print(f"[thermal] 시작부터 팬 가동: '{cmd}'")
                ser.write((cmd + "\n").encode())
            else:
                print(f"[thermal] 대기 중 — T>={args.heat_until_temp:.1f}C 도달하면 자동으로 "
                      f"'F {args.intake} {args.exhaust}' 적용 (그 전까지 팬 정지 유지)")
                ser.write(b"0\n")

            start = time.monotonic()
            if camera is not None:
                camera.begin()
            while time.monotonic() - start < duration_sec:
                if camera is not None:
                    camera.check()
                keys.poll()
                if keys.quit or (args.stop_file is not None and args.stop_file.exists()):
                    interrupted = True
                    break
                elapsed = time.monotonic() - start
                while pending_events and pending_events[0][0] * 60 <= elapsed:
                    _, msg = pending_events.pop(0)
                    print(f"[thermal] >>> 이벤트: {msg}")

                raw = ser.readline()
                if not raw:
                    continue
                d = parse_data_line(raw.decode("utf-8", "replace").strip())
                if d is None:
                    continue
                temp_ok = not d["bme_err"] and math.isfinite(d["temp_c"])
                rh_ok = not d["bme_err"] and math.isfinite(d["rh_pct"])
                reached = (not fans_active and args.heat_until_temp is not None
                           and temp_ok and d["temp_c"] >= args.heat_until_temp)
                if reached:
                    keys.heat_load = 0
                    print(f"[thermal] >>> {d['temp_c']:.1f}C 도달! 드라이기를 직접 끄세요. "
                          "Heat_Load=0 기록 후 팬을 적용합니다.")

                ts = datetime.now().isoformat(timespec="milliseconds")
                writer.writerow([ts, d["temp_c"] if temp_ok else "", d["rh_pct"] if rh_ok else "",
                                 d["duty_out"], d["duty_in"], keys.heat_load,
                                 d["rpm_out"], d["rpm_in"], d["pm2_5"]])
                f.flush()  # 통신 오류나 중단 전까지 받은 샘플을 즉시 보존
                row_count += 1
                if reached:
                    ser.write(f"F {args.intake} {args.exhaust}\n".encode())
                    fans_active = True
                print(f"[{row_count}] t={ts} T={d['temp_c']:.1f}C RH={d['rh_pct']:.1f}% "
                      f"in={d['duty_in']}%/{d['rpm_in']}rpm out={d['duty_out']}%/{d['rpm_out']}rpm "
                      f"heat={keys.heat_load} PM2.5={d['pm2_5']}")
    except StartCheckCancelled:
        interrupted = True
        print("[start-check] 취소 — 센서 기록·촬영을 시작하지 않았습니다")
    except KeyboardInterrupt:
        interrupted = True
        print("\n[thermal] 사용자 중단(Ctrl+C)")
    except (serial.SerialException, OSError) as exc:
        failure = str(exc)
    finally:
        if ser is not None:
            try:
                ser.write(b"0\n")
                time.sleep(0.3)
            except (serial.SerialException, OSError) as exc:
                failure = failure or str(exc)
                print(f"[thermal] 정지 명령 전송 실패 — 팬 상태를 직접 확인하세요: {exc}", file=sys.stderr)
            finally:
                try:
                    ser.close()
                except (serial.SerialException, OSError) as exc:
                    failure = failure or str(exc)

        if camera is not None:
            try:
                camera.close()
            except OSError as exc:
                failure = failure or str(exc)
            print(f"[thermal] 사진 {camera.count}장 저장 -> {image_path}")
            if camera.count == 0 and row_count > 0:
                failure = failure or "카메라 사진이 저장되지 않았습니다"

    print(f"[thermal] 종료 -> {row_count}개 샘플, {out_path}")
    if failure is not None:
        raise SystemExit(f"[thermal] 오류: {failure}")
    if interrupted:
        return False
    if row_count == 0:
        raise SystemExit("[thermal] DATA 샘플 없음 — 포트와 new_fan_t 펌웨어를 확인하세요")
    if not fans_active:
        raise SystemExit("[thermal] 목표온도 미도달 — 냉각 단계 미실행. 가열을 연장하지 말고 기록을 확인하세요")
    return not interrupted


if __name__ == "__main__":
    main()
