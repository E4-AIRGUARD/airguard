"""One 300-second smoke run after the user reports smoke generation.

Requires a saved PM2.5 0..20 check from BEFORE smoke generation. Does not
reapply the baseline range after smoke. BME280 is excluded; Temp/Humidity
remain blank in the unchanged CSV schema. Camera records only the run.
"""
import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from measure_clean_air_matrix import COMBINATIONS
from measure_thermal_response import CSV_HEADER, parse_data_line
from thermal_camera import CameraRecorder


def valid_pm(d):
    return d is not None and d['pms_stale'] == 0 and 0 <= d['pm2_5'] <= 65535


def write_status(path, state):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers/indexers can briefly hold the destination without delete sharing.
    # Keep atomic publication; a persistent write failure must still stop the run.
    for attempt in range(11):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 10:
                raise
            time.sleep(0.05)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session-dir', type=Path, required=True)
    p.add_argument('--combination', type=int, choices=range(1, 10), required=True)
    p.add_argument('--pre-smoke-check', type=Path, required=True)
    args = p.parse_args(argv)
    baseline_report = json.loads(args.pre_smoke_check.read_text(encoding='utf-8-sig'))
    baseline = baseline_report.get('last')
    if (baseline_report.get('combination') != args.combination
            or not baseline_report.get('ready') or not valid_pm(baseline)
            or not 0 <= baseline['pm2_5'] <= 20):
        raise SystemExit('Valid pre-smoke PM2.5 0..20 check for this combination required')
    check_time = datetime.fromisoformat(baseline_report['checked_at'])
    age = (datetime.now().astimezone() - check_time).total_seconds()
    if not 0 <= age <= 900:
        raise SystemExit('Pre-smoke check expired (15 minutes); check again before generating smoke')
    if (args.session_dir / 'STOP').exists():
        raise SystemExit('User requested STOP')
    name, intake, exhaust = COMBINATIONS[args.combination - 1]
    run = args.session_dir / f'{args.combination:02d}_{name}'
    run.mkdir(parents=True, exist_ok=False)
    csvpath = run / f'{args.combination:02d}_{name}.csv'
    state = dict(status='preparing', combination=args.combination, intake=intake,
                 exhaust=exhaust, duration_sec=300, rows=0, images=0,
                 csv=str(csvpath.resolve()), images_dir=str((run / 'images').resolve()),
                 pre_smoke_check=str(args.pre_smoke_check.resolve()), pre_smoke_pm25=baseline['pm2_5'],
                 bme_enabled=False, temperature_humidity_recorded=False,
                 trigger='User reported smoke generation completed',
                 actual_smoke_generation_timestamp=None, heat_load=0, error=None, stop_verified=False)
    ser = cam = f = None
    samples = []

    def save():
        state.update(updated=datetime.now().isoformat(timespec='milliseconds'), images=cam.count if cam else 0)
        write_status(run / 'status.json', state)

    def read():
        d = parse_data_line(ser.readline().decode('utf-8', 'replace').strip())
        if d:
            for key in ('temp_c', 'rh_pct', 'bme_err'):
                d.pop(key, None)
        return d

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
        if not valid_pm(d):
            raise OSError('Fresh PMS5003 reading unavailable')
        if (args.session_dir / 'STOP').exists() or (run / 'STOP').exists():
            raise InterruptedError('User requested STOP')
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
            if not valid_pm(d):
                raise OSError('PMS5003 reading stale or invalid')
            writer.writerow([datetime.now().isoformat(timespec='milliseconds'), '',
                             '', d['duty_out'],
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
                           for k in ('rpm_in', 'rpm_out', 'pm2_5')}
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
                if not state['stop_verified']:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        d = read()
                        if d and all(d[k] == 0 for k in ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')):
                            state.update(stop_verified=True, stop_sample=d)
                            break
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
