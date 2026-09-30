"""Record a 300 s fan-on cycle: 30 s baseline, manual heating to 40 C, recovery.

One invocation measures one selected fan pair. No GUI or automatic next run.
Heat_Load denotes the heating instruction, not measured hairdryer power.
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

DURATION_SEC = 300
BASELINE_SEC = 30
TARGET_C = 40.0
DEFAULT_ROOT = Path(__file__).resolve().parent / 'data/clean_air_fan_matrix/heat'


def timestamp():
    return datetime.now().isoformat(timespec='milliseconds')


def notify(message):
    print(message, flush=True)
    try:
        import winsound
        winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC)
    except (ImportError, RuntimeError):
        pass


def valid_sensor(d):
    return (d is not None and not d['bme_err']
            and math.isfinite(d['temp_c']) and -40 <= d['temp_c'] <= 85
            and math.isfinite(d['rh_pct']) and 0 <= d['rh_pct'] <= 100)


def fans_stopped(d):
    return d is not None and all(d[k] == 0 for k in
                               ('duty_in', 'duty_out', 'rpm_in', 'rpm_out'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--combination', type=int, choices=range(1, 10), required=True)
    parser.add_argument('--session-dir', type=Path,
                        default=DEFAULT_ROOT / datetime.now().strftime('cycle_%Y%m%d_%H%M%S_%f'))
    parser.add_argument('--port', default='COM7')
    parser.add_argument('--camera-index', type=int, default=1)
    args = parser.parse_args(argv)
    name, intake, exhaust = COMBINATIONS[args.combination - 1]
    run = args.session_dir / f'{args.combination:02d}_{name}'
    run.mkdir(parents=True, exist_ok=False)
    csvpath = run / f'{args.combination:02d}_{name}.csv'
    state = dict(status='preparing', stage='waiting', combination=args.combination,
                 intake=intake, exhaust=exhaust, duration_sec=DURATION_SEC,
                 baseline_sec=BASELINE_SEC, target_c=TARGET_C,
                 rows=0, images=0, heat_load=0, target_reached=False,
                 cooling_rows=0, returned_to_start_range=False,
                 actual_heater_on_timestamp=None, actual_heater_off_timestamp=None,
                 heat_load_source='manual_heater_instruction_only',
                 csv=str(csvpath.resolve()), images_dir=str((run / 'images').resolve()),
                 error=None, stop_verified=False)
    ser = cam = output = None
    start = None

    def save():
        state.update(updated=timestamp(), images=cam.count if cam else 0)
        tmp = run / 'status.tmp'
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(run / 'status.json')

    def event(kind, **details):
        item = dict(Time=timestamp(), event=kind,
                    elapsed_sec=round(time.monotonic() - start, 3) if start is not None else None,
                    **details)
        with (run / 'events.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(item, ensure_ascii=False) + '\n')

    def read():
        return parse_data_line(ser.readline().decode('utf-8', 'replace').strip())

    def check_stop():
        if (run / 'STOP').exists() or (args.session_dir / 'STOP').exists():
            raise InterruptedError('User requested STOP')

    def heat_cue(elapsed):
        if state['stage'] == 'baseline' and BASELINE_SEC <= elapsed < DURATION_SEC:
            state.update(stage='heating', heat_load=1)
            event('heat_on_instruction')
            notify('30초 정상 구간 완료: 지금 드라이기로 가열하세요. 40℃ 도달 시 끄도록 알립니다.')

    print(f'RUN_DIR={run.resolve()}', flush=True)
    save()
    try:
        check_stop()
        ser = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
        ser.dtr = ser.rts = False
        ser.port = args.port
        ser.open()
        ser.write(b'0\n')
        cam = CameraRecorder(run / 'images', index=args.camera_index, interval=1.0)
        cam.open()
        ser.reset_input_buffer()
        state['status'] = 'waiting_for_start'
        save()
        print('온도 22~26℃ 및 팬 정지를 확인합니다. 아직 가열하지 마세요.', flush=True)
        last = time.monotonic()
        while True:
            check_stop()
            cam.check()
            d = read()
            now = time.monotonic()
            if d is None:
                if now - last > 5:
                    raise OSError('No sensor DATA for 5 seconds')
                continue
            last = now
            if not valid_sensor(d):
                raise OSError('Invalid BME280 temperature/humidity')
            state['last_sensor'] = d
            save()
            if 22 <= d['temp_c'] <= 26 and fans_stopped(d):
                state['preheat_sample'] = d
                break
        check_stop()
        output = csvpath.open('x', newline='', encoding='utf-8')
        writer = csv.writer(output)
        writer.writerow(CSV_HEADER)
        output.flush()
        ser.reset_input_buffer()
        ser.write(f'F {intake} {exhaust}\n'.encode())
        start = last = time.monotonic()
        cam.begin()
        state.update(status='running', stage='baseline', started=timestamp())
        event('measurement_started', intake=intake, exhaust=exhaust)
        save()
        print('300초 측정·촬영 시작. 처음 30초는 가열하지 마세요.', flush=True)
        while time.monotonic() - start < DURATION_SEC:
            check_stop()
            cam.check()
            heat_cue(time.monotonic() - start)
            d = read()
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= DURATION_SEC:
                break
            if d is None:
                if now - last > 5:
                    raise OSError('No sensor DATA for 5 seconds')
                continue
            last = now
            if not valid_sensor(d):
                raise OSError('Invalid BME280 temperature/humidity')
            heat_cue(elapsed)
            if state['stage'] == 'heating' and d['temp_c'] >= TARGET_C:
                state.update(stage='recovery', heat_load=0, target_reached=True,
                             target_elapsed_sec=round(elapsed, 3), target_temp_c=d['temp_c'])
                event('heat_off_instruction', reason='target_reached', temp_c=d['temp_c'])
                notify('40℃ 도달: 지금 드라이기를 끄세요. 팬은 같은 속도로 유지하며 회복을 측정합니다.')
            writer.writerow([timestamp(), d['temp_c'], d['rh_pct'], d['duty_out'],
                             d['duty_in'], state['heat_load'], d['rpm_out'], d['rpm_in'],
                             d['pm2_5'] if not d['pms_stale'] else ''])
            output.flush()
            state.update(rows=state['rows'] + 1, last_sensor=d, elapsed_sec=round(elapsed, 3),
                         peak_temp_c=max(state.get('peak_temp_c', d['temp_c']), d['temp_c']))
            if state['stage'] == 'recovery' and elapsed > state['target_elapsed_sec']:
                state['cooling_rows'] += 1
                state['returned_to_start_range'] = 22 <= d['temp_c'] <= 26
            save()
            if elapsed > 8 and ((d['duty_in'], d['duty_out']) != (intake, exhaust)
                                or d['rpm_in'] <= 0 or d['rpm_out'] <= 0):
                raise OSError('Fan duty/RPM verification failed')
        cam.pause()
        ser.write(b'0\n')
        output.close()
        output = None
        state.update(status='stopping', elapsed_sec=round(time.monotonic() - start, 3),
                     measurement_ended=timestamp())
        if state['heat_load']:
            event('heat_off_instruction', reason='measurement_deadline')
        state['heat_load'] = 0
        event('measurement_ended')
        notify('300초 측정 종료. 드라이기가 켜져 있다면 끄세요.')
        save()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            d = read()
            if fans_stopped(d):
                state.update(stop_verified=True, stop_sample=d)
                break
        if not state['stop_verified']:
            raise OSError('Fan stop not verified')
        if not state['rows'] or not cam.count:
            raise OSError('Sensor or image data missing')
        if not state['target_reached']:
            outcome = 'target_not_reached'
        elif not state['cooling_rows']:
            outcome = 'no_recovery_samples'
        elif not state['returned_to_start_range']:
            outcome = 'recovery_not_finished'
        else:
            outcome = 'returned_to_start_range'
        state.update(status='complete', stage='finished', outcome=outcome)
        event('complete', outcome=outcome)
    except BaseException as exc:
        state.update(status='stopped' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                     error=str(exc))
        event('aborted', error=str(exc))
        notify('측정 중단: 드라이기를 끄세요. ' + str(exc))
    finally:
        def cleanup(action):
            try:
                action()
            except Exception as exc:
                state.update(status='failed', error=(state['error'] or '') + '; cleanup: ' + str(exc))
        if cam:
            cleanup(cam.pause)
        if ser is not None and ser.is_open:
            cleanup(lambda: ser.write(b'0\n'))
            if not state['stop_verified']:
                def verify_stop():
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        d = read()
                        if fans_stopped(d):
                            state.update(stop_verified=True, stop_sample=d)
                            return
                cleanup(verify_stop)
            cleanup(ser.close)
        if output:
            cleanup(output.close)
        if cam:
            cleanup(cam.close)
        state.update(ended=timestamp(), heat_load=0)
        save()
        if state['status'] == 'complete':
            descriptions = {
                'target_not_reached': '40℃ 미도달',
                'no_recovery_samples': '40℃ 도달, 회복 구간 표본 없음',
                'recovery_not_finished': '40℃ 도달, 종료 시 22~26℃까지 회복되지 않음',
                'returned_to_start_range': '40℃ 도달 후 종료 시 22~26℃로 복귀',
            }
            print(f"측정 완료: 흡기 {intake}% / 배기 {exhaust}%. "
                  + descriptions[state['outcome']], flush=True)
        print('RESULT ' + json.dumps(state, ensure_ascii=False), flush=True)
    if state['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
