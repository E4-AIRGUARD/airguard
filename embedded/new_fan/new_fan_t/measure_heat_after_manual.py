"""Legacy no-window cooling measurement after manual heating ended.

For the 2026-09-30 baseline -> 40 C -> recovery cycle totaling 300 seconds,
use measure_heat_cycle.py instead. This entrypoint preserves the old protocol.

Use a saved pre-heating temperature check; do not wait for 22..26 C again after
heating. Heater-off time is not instrumented. CSV Heat_Load is zero throughout
this 300-second cooling segment. Camera writes only during that segment.
"""
import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path

import serial
from measure_clean_air_matrix import COMBINATIONS
from measure_thermal_response import CSV_HEADER, parse_data_line
from thermal_camera import CameraRecorder


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session-dir', type=Path, required=True)
    p.add_argument('--combination', type=int, choices=range(1, 10), required=True)
    p.add_argument('--preheat-check', type=Path, required=True)
    args = p.parse_args()
    baseline_report = json.loads(args.preheat_check.read_text(encoding='utf-8-sig'))
    baseline = baseline_report.get('stop_sample') or baseline_report.get('last') or baseline_report.get('summary', {}).get('last')
    if not baseline or baseline['bme_err'] or not 22 <= baseline['temp_c'] <= 26:
        raise SystemExit('Saved pre-heating temperature check is not valid')
    name, intake, exhaust = COMBINATIONS[args.combination - 1]
    run = args.session_dir / f'{args.combination:02d}_{name}'
    run.mkdir(parents=True, exist_ok=False)
    csvpath = run / f'{args.combination:02d}_{name}.csv'
    state = dict(status='preparing', combination=args.combination, intake=intake,
                 exhaust=exhaust, duration_sec=300, rows=0, images=0,
                 csv=str(csvpath.resolve()), images_dir=str((run / 'images').resolve()),
                 preheat_check=str(args.preheat_check.resolve()), preheat_temp_c=baseline['temp_c'],
                 trigger='User confirmed 5 seconds of manual heating completed',
                 actual_heater_off_timestamp=None, heat_load=0, error=None, stop_verified=False)
    ser = cam = f = None
    samples = []

    def save():
        state.update(updated=datetime.now().isoformat(timespec='milliseconds'), images=cam.count if cam else 0)
        tmp = run / 'status.tmp'
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(run / 'status.json')

    def read():
        return parse_data_line(ser.readline().decode('utf-8', 'replace').strip())

    def valid(d):
        return d is not None and not d['bme_err'] and math.isfinite(d['temp_c']) and -40 <= d['temp_c'] <= 85

    print(f'RUN_DIR={run.resolve()}', flush=True)
    save()
    try:
        ser = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
        ser.dtr = ser.rts = False
        ser.port = 'COM7'
        ser.open()
        ser.reset_input_buffer()
        cam = CameraRecorder(run / 'images', index=1, interval=1.0)
        cam.open()
        ser.reset_input_buffer()
        check_end = time.monotonic() + 5
        d = None
        while time.monotonic() < check_end:
            d = read()
            if d is not None:
                break
        if not valid(d):
            raise OSError('Valid BME280 reading unavailable')
        f = csvpath.open('x', newline='', encoding='utf-8')
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        f.flush()
        ser.write(f'F {intake} {exhaust}\n'.encode())
        start = last = time.monotonic()
        cam.begin()
        state.update(status='running', started=datetime.now().isoformat(timespec='milliseconds'), first_sensor=d)
        save()
        print('STARTED ' + json.dumps(state, ensure_ascii=False), flush=True)
        while time.monotonic() - start < 300:
            cam.check()
            if (args.session_dir / 'STOP').exists() or (run / 'STOP').exists():
                raise InterruptedError('User requested STOP')
            d = read()
            now = time.monotonic()
            if now - start >= 300:
                break
            if d is None:
                if now - last > 5:
                    raise OSError('No sensor DATA for 5 seconds')
                continue
            last = now
            if not valid(d):
                raise OSError('Invalid BME280 reading during measurement')
            writer.writerow([datetime.now().isoformat(timespec='milliseconds'), d['temp_c'],
                             d['rh_pct'] if math.isfinite(d['rh_pct']) else '', d['duty_out'],
                             d['duty_in'], 0, d['rpm_out'], d['rpm_in'],
                             d['pm2_5'] if not d['pms_stale'] else ''])
            f.flush()
            samples.append(d)
            state.update(rows=len(samples), last_sensor=d, elapsed_sec=round(now - start, 2))
            save()
            if now - start > 8 and ((d['duty_in'], d['duty_out']) != (intake, exhaust)
                                   or d['rpm_in'] <= 0 or d['rpm_out'] <= 0):
                raise OSError('Fan duty/RPM verification failed')
        cam.pause()
        ser.write(b'0\n')
        state.update(status='stopping', measurement_ended=datetime.now().isoformat(timespec='milliseconds'),
                     elapsed_sec=round(time.monotonic() - start, 2))
        f.close()
        f = None
        save()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            d = read()
            if d and all(d[k] == 0 for k in ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')):
                state.update(stop_verified=True, stop_sample=d)
                break
        if not state['stop_verified']:
            raise OSError('Fan stop not verified')
        if not samples or not cam.count:
            raise OSError('Sensor or image data missing')
        state['status'] = 'complete'
        state['ranges'] = {k: [min(s[k] for s in samples), max(s[k] for s in samples)]
                           for k in ('temp_c', 'rh_pct', 'rpm_in', 'rpm_out', 'pm2_5')}
    except BaseException as exc:
        state['status'] = 'stopped' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed'
        state['error'] = str(exc)
    finally:
        if cam:
            try:
                cam.pause()
            except Exception as exc:
                state.update(status='failed', error=str(exc))
        if ser is not None and ser.is_open:
            try:
                ser.write(b'0\n')
            except Exception as exc:
                state.update(status='failed', error=str(exc))
            finally:
                ser.close()
        if f:
            f.close()
        if cam:
            try:
                cam.close()
            except Exception as exc:
                state.update(status='failed', error=str(exc))
        state['ended'] = datetime.now().isoformat(timespec='milliseconds')
        save()
        print('RESULT ' + json.dumps(state, ensure_ascii=False), flush=True)
    if state['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
