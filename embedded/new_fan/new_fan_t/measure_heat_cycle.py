"""Record 300 s: 30 s baseline, 30 s manual heating, recovery; then same-duty cooling.

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
HEAT_SEC = 30
COOLING_TIMEOUT_SEC = 3600
DEFAULT_ROOT = Path(__file__).resolve().parent / 'data/clean_air_fan_matrix/heat'


def timestamp():
    return datetime.now().isoformat(timespec='milliseconds')


def save_status(path, state):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers can briefly prevent an atomic replacement.
    for attempt in range(11):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 10:
                raise
            time.sleep(0.05)


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
                 baseline_sec=BASELINE_SEC, target_c=TARGET_C, heating_duration_sec=HEAT_SEC,
                 protocol=f'fixed_{HEAT_SEC}s_heat_then_same_duty_cooling',
                 rows=0, images=0, heat_load=0, target_reached=False,
                 cooling_rows=0, returned_to_start_range=False,
                 actual_heater_on_timestamp=None, actual_heater_off_timestamp=None,
                 heat_load_source='manual_heater_instruction_only',
                 csv=str(csvpath.resolve()), images_dir=str((run / 'images').resolve()),
                 error=None, stop_verified=False)
    ser = cam = output = None
    start = None
    cooling = None
    cooling_dir = args.session_dir / 'cooling' / f'{args.combination:02d}_{name}'
    for n, (pair_name, _, _) in enumerate(COMBINATIONS, 1):
        (args.session_dir / 'cooling' / f'{n:02d}_{pair_name}').mkdir(parents=True, exist_ok=True)
    if any(cooling_dir.iterdir()):
        raise FileExistsError(f'Cooling folder already contains data: {cooling_dir}')

    def save():
        state.update(updated=timestamp(), images=cam.count if cam else 0)
        save_status(run / 'status.json', state)
        if cooling is not None:
            cooling['updated'] = timestamp()
            save_status(cooling_dir / 'status.json', cooling)

    def event(kind, **details):
        item = dict(Time=timestamp(), event=kind,
                    elapsed_sec=round(time.monotonic() - start, 3) if start is not None else None,
                    **details)
        with (run / 'events.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(item, ensure_ascii=False) + '\n')

    def read():
        return parse_data_line(ser.readline().decode('utf-8', 'replace').strip())

    def check_stop():
        if ((run / 'STOP').exists() or (args.session_dir / 'STOP').exists()
                or (cooling_dir / 'STOP').exists()):
            raise InterruptedError('User requested STOP')

    def heat_off(elapsed, reason, sample=None):
        state.update(stage='recovery', heat_load=0,
                     recovery_start_elapsed_sec=round(elapsed, 3),
                     heat_stop_reason=reason,
                     heating_instruction_duration_sec=round(elapsed - state['heat_start_elapsed_sec'], 3))
        event('heat_off_instruction', reason=reason,
              temp_c=sample['temp_c'] if sample else None)
        notify(f'{HEAT_SEC}초 가열 종료: 지금 드라이기를 끄세요. 팬은 같은 속도로 유지하며 회복을 측정합니다.')
        save()

    def heat_cue(elapsed):
        if state['stage'] == 'baseline' and BASELINE_SEC <= elapsed < DURATION_SEC:
            state.update(stage='heating', heat_load=1, heat_start_elapsed_sec=elapsed)
            event('heat_on_instruction')
            notify(f'{BASELINE_SEC}초 정상 구간 완료: 지금 드라이기로 {HEAT_SEC}초간 가열하세요. {HEAT_SEC}초 종료 시 끄도록 알립니다.')
            save()
        if (state['stage'] == 'heating'
                and elapsed - state['heat_start_elapsed_sec'] >= HEAT_SEC):
            heat_off(elapsed, 'fixed_duration_elapsed')

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
            if not state['target_reached'] and d['temp_c'] >= TARGET_C:
                state.update(target_reached=True, target_elapsed_sec=round(elapsed, 3),
                             target_temp_c=d['temp_c'],
                             target_reached_during_heating=state['stage'] == 'heating')
                event('temperature_40_reached', temp_c=d['temp_c'], stage=state['stage'])
            writer.writerow([timestamp(), d['temp_c'], d['rh_pct'], d['duty_out'],
                             d['duty_in'], state['heat_load'], d['rpm_out'], d['rpm_in'],
                             d['pm2_5'] if not d['pms_stale'] else ''])
            output.flush()
            state.update(rows=state['rows'] + 1, last_sensor=d, elapsed_sec=round(elapsed, 3),
                         peak_temp_c=max(state.get('peak_temp_c', d['temp_c']), d['temp_c']))
            if state['stage'] == 'recovery' and elapsed > state['recovery_start_elapsed_sec']:
                state['cooling_rows'] += 1
                state['returned_to_start_range'] = 22 <= d['temp_c'] <= 26
            save()
            if elapsed > 8 and ((d['duty_in'], d['duty_out']) != (intake, exhaust)
                                or d['rpm_in'] <= 0 or d['rpm_out'] <= 0):
                raise OSError('Fan duty/RPM verification failed')
        cam.pause()
        output.close()
        output = None
        state.update(status='cooling', stage='post_measurement_cooling',
                     elapsed_sec=round(time.monotonic() - start, 3),
                     measurement_ended=timestamp(), measurement_complete=True,
                     measurement_last_sensor=state.get('last_sensor'))
        if state['heat_load']:
            event('heat_off_instruction', reason='measurement_deadline')
        state['heat_load'] = 0
        event('measurement_ended')
        notify('300초 측정·촬영 완료. 같은 팬 속도로 추가 냉각을 기록합니다. 드라이기는 꺼 두세요.')
        if not state['rows'] or not cam.count:
            raise OSError('Sensor or image data missing')
        cooling_start = last = time.monotonic()
        cooling = dict(status='cooling', started=timestamp(), intake=intake, exhaust=exhaust,
                       initial_sensor=state['last_sensor'], rows=0, elapsed_sec=0,
                       criterion='22 <= temp_c <= 26 for 3 consecutive valid samples',
                       stop_verified=False, next_start_ready=False,
                       continuous_from_measurement=True, photos_saved=0)
        state['cooling_dir'] = str(cooling_dir.resolve())
        event('cooling_started', intake=intake, exhaust=exhaust)
        save()
        consecutive = 0
        with (cooling_dir / 'cooling.csv').open('x', newline='', encoding='utf-8') as f, \
                (cooling_dir / 'samples.jsonl').open('x', encoding='utf-8') as log:
            cw = csv.writer(f)
            cw.writerow(CSV_HEADER)
            while time.monotonic() - cooling_start < COOLING_TIMEOUT_SEC:
                check_stop()
                d = read()
                now = time.monotonic()
                if d is None:
                    if now - last > 5:
                        raise OSError('No sensor DATA for 5 seconds during cooling')
                    continue
                last = now
                if not valid_sensor(d):
                    raise OSError('Invalid BME280 temperature/humidity during cooling')
                at = timestamp()
                cooling.update(rows=cooling['rows'] + 1, last_sensor=d,
                               elapsed_sec=round(now - cooling_start, 3))
                cw.writerow([at, d['temp_c'], d['rh_pct'], d['duty_out'], d['duty_in'],
                             0, d['rpm_out'], d['rpm_in'], d['pm2_5'] if not d['pms_stale'] else ''])
                f.flush()
                log.write(json.dumps(dict(Time=at, elapsed_sec=cooling['elapsed_sec'], **d)) + '\n')
                log.flush()
                if ((d['duty_in'], d['duty_out']) != (intake, exhaust)
                        or d['rpm_in'] <= 0 or d['rpm_out'] <= 0):
                    raise OSError('Fan duty/RPM verification failed during cooling')
                consecutive = consecutive + 1 if 22 <= d['temp_c'] <= 26 else 0
                cooling['consecutive_in_range'] = consecutive
                save()
                if consecutive >= 3:
                    cooling.update(status='stopping', threshold_elapsed_sec=cooling['elapsed_sec'],
                                   threshold_at=at, threshold_sensor=d)
                    break
            else:
                raise TimeoutError('Cooling start condition not reached within 3600 seconds')
        ser.write(b'0\n')
        state['status'] = 'stopping'
        event('cooling_threshold_reached', cooling_elapsed_sec=cooling['threshold_elapsed_sec'])
        save()
        deadline = time.monotonic() + 10
        stopped_count = 0
        while time.monotonic() < deadline:
            d = read()
            # Firmware emits a [FAN] text line before each DATA sample.
            # Only sensor samples count toward consecutive stopped readings.
            if d is None:
                continue
            stopped_count = stopped_count + 1 if fans_stopped(d) else 0
            if stopped_count >= 2:
                state.update(stop_verified=True, stop_sample=d)
                break
        if not state['stop_verified']:
            raise OSError('Fan stop not verified')
        ready = valid_sensor(d) and 22 <= d['temp_c'] <= 26
        cooling.update(status='complete', ended=timestamp(), stop_verified=True, stop_sensor=d,
                       next_start_ready=bool(ready), total_elapsed_sec=round(time.monotonic()-cooling_start, 3))
        state['next_start_ready'] = bool(ready)
        notify(f"추가 냉각 완료: {cooling['threshold_elapsed_sec']:.1f}초, 종료 온도 {d['temp_c']:.2f}℃. "
               + ('다음 시작 조건 충족. 팬 정지 확인.' if ready else '팬 정지 확인. 온도 시작 조건은 재확인이 필요합니다.'))
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
        if cooling is not None:
            cooling['serial_closed'] = ser is None or not ser.is_open
            if state['status'] in ('failed', 'stopped'):
                cooling.update(status=state['status'], error=state['error'], ended=timestamp(),
                               stop_verified=state['stop_verified'])
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
