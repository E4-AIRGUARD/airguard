"""60s smoke then confirmed removal and 30s observation, fans OFF throughout.

Start is manual after fresh PM <=20 checks. No command ever starts a fan.
Sensor CSV columns and native sensor sampling frequency are preserved.
"""
import csv
import argparse
import json
import math
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from measure_smoke_cycle import (SerialLines, SmokeCycle, parse_pms, read_commands,
                                 stop_fans, timestamp)
from measure_smoke_after_manual import write_status
from measure_thermal_response import CSV_HEADER, parse_data_line


class OffSmokeTiming:
    def __init__(self, started):
        self.started = started
        self.removed = None
        self.phase = 'smoking'

    def tick(self, now):
        if self.phase == 'smoking' and now-self.started >= 60:
            self.phase = 'await_remove'
            return 'remove_requested'
        if self.removed is not None and now-self.removed >= 30:
            self.phase = 'complete'
            return 'complete'
        return None

    def confirm_removal(self, now):
        if self.phase != 'await_remove':
            raise ValueError('Removal confirmation requires the 60-second removal cue')
        self.removed = now
        self.phase = 'post_removal'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-smoke-check', type=Path)
    args=parser.parse_args(argv)
    verified_baseline=None
    if args.pre_smoke_check:
        evidence=json.loads(args.pre_smoke_check.read_text(encoding='utf-8-sig'))
        baseline=evidence.get('pre_start_sensor',{})
        checked=datetime.fromisoformat(baseline.get('Time',''))
        age=(datetime.now().astimezone()-checked).total_seconds()
        if (not evidence.get('stop_verified') or not 0<=baseline.get('pm25',-1)<=20
                or not 0<=age<=900):
            raise ValueError('Pre-smoke baseline invalid or older than 15 minutes')
        verified_baseline=baseline
    import serial
    from thermal_camera import CameraRecorder
    run = Path(__file__).resolve().parent / 'data' / 'clean_air_fan_matrix' / 'smoke_off90' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    run.mkdir(parents=True, exist_ok=False)
    state = dict(status='preparing', protocol='fans_off_smoke60_remove_confirm_off30',
                 nominal_duration_sec=90, smoke_sec=60, post_removal_sec=30,
                 camera_interval_sec=1.0, camera_rotation_deg=180,
                 start_pm25_max=20, start_temperature_range=None,
                 rows=0, images=0, stop_verified=False, error=None)
    if args.pre_smoke_check:
        state.update(pre_smoke_check_path=str(args.pre_smoke_check),
                     pre_start_sensor=verified_baseline,
                     timing_note='Recording starts on command receipt; smoke was already generated. Baseline was verified before smoke, not after insertion.')
    commands = queue.Queue()
    threading.Thread(target=read_commands, args=(commands,), daemon=True).start()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
    port.dtr = port.rts = False
    port.port = 'COM7'
    cam = None
    started = None
    timing = None
    armed_at = None
    armed_sensor = None
    latest = None
    last_data_at = None
    ready_announced = False
    stop_count = 0
    handles = []

    def save():
        state.update(updated=timestamp(), images=cam.count if cam else 0)
        write_status(run / 'status.json', state)

    try:
        port.open()
        if not stop_fans(port):
            raise OSError('Fan stop could not be verified')
        state['stop_verified'] = True
        cam = CameraRecorder(run / 'images', index=1, interval=1.0, rotation_deg=180,
                             manual_exposure=-6)
        state['camera_manual_exposure'] = -6
        cam.open()
        port.reset_input_buffer()
        reader = SerialLines(port)
        gate = SmokeCycle(time.monotonic())
        csvfile = (run / f'sensors_{run.name}.csv').open('x', encoding='utf-8', newline='')
        handles.append(csvfile)
        writer = csv.writer(csvfile)
        writer.writerow(CSV_HEADER)
        csvfile.flush()
        events = (run / 'events.jsonl').open('x', encoding='utf-8')
        handles.append(events)
        samples = (run / 'pms_samples.jsonl').open('x', encoding='utf-8')
        handles.append(samples)

        def event(name, **details):
            events.write(json.dumps(dict(Time=timestamp(), event=name, **details), ensure_ascii=False)+'\n')
            events.flush()

        state['status'] = 'waiting_start'
        print('RUN_DIR '+str(run), flush=True)
        save()
        while True:
            now = time.monotonic()
            cam.check()
            if (run / 'STOP').exists():
                raise InterruptedError('STOP requested')
            if started is not None:
                if not gate.fresh(now) or last_data_at is None or now-last_data_at > 5:
                    raise OSError('Fresh sensor telemetry lost')
                if now-started > 600:
                    raise TimeoutError('Removal not confirmed within run limit')
                transition = timing.tick(now)
                state['phase'] = timing.phase
                if transition == 'remove_requested':
                    event('remove_requested', elapsed_sec=now-started)
                    print('\aREMOVE_NOW: 연기 생성 60초 종료. 끄고 제거한 뒤 removed 입력.', flush=True)
                if transition == 'complete':
                    cam.pause()
                    state.update(status='stopping', elapsed_sec=now-started, measurement_ended=timestamp())
                    event('measurement_ended', elapsed_sec=now-started)
                    state.update(actual_smoke_confirmation_sec=timing.removed-started,
                                 removal_confirmation_delay_sec=timing.removed-started-60,
                                 actual_post_removal_sec=now-timing.removed)
                    print('\aCOMPLETE: 제거 후 팬 OFF 30초 기록 완료.', flush=True)
                    break
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            if command in ('quit', 'q', 'stop'):
                raise InterruptedError('User stopped experiment')
            fan_ready = stop_count >= 2 and last_data_at is not None and now-last_data_at <= 2
            camera_ready = cam.cadence_summary()
            state['camera_preflight'] = camera_ready
            if command == 'arm' and started is None:
                baseline_valid = (verified_baseline is not None and
                    0 <= (datetime.now().astimezone()-datetime.fromisoformat(verified_baseline['Time'])).total_seconds() <= 900)
                if not ((gate.ready(now) or (baseline_valid and gate.fresh(now))) and fan_ready and camera_ready['ready']):
                    print('NOT_READY: fresh PM<=20 x3, fans OFF and camera cadence required', flush=True)
                else:
                    armed_at = now
                    armed_sensor = verified_baseline if baseline_valid else dict(Time=timestamp(),pm25=gate.last['pm25'],temperature=latest['temp_c'])
                    state.update(status='armed',pre_start_sensor=armed_sensor)
                    event('start_conditions_verified', pm25=armed_sensor['pm25'],
                          temperature=armed_sensor['temperature'])
                    print('ARMED: announce smoke cue, then start within 30 seconds',flush=True)
                    save()
            elif command == 'start' and started is None:
                # PM may rise after the human smoke cue; the clean baseline was
                # verified by arm BEFORE that cue. Never silently invent a baseline.
                if (armed_at is None or now-armed_at > 30 or not fan_ready
                        or camera_ready['last_age_sec'] is None or camera_ready['last_age_sec'] >= 1.0):
                    print('NOT_READY: arm expired or hardware unavailable',flush=True)
                else:
                    started = now
                    timing = OffSmokeTiming(now)
                    state.update(status='running', started=timestamp(),
                                 pre_start_sensor=armed_sensor)
                    event('measurement_started_smoke_insertion_cue', pm25=gate.last['pm25'])
                    cam.begin()
                    print('\aSTARTED: 지금 연기를 생성해 주세요. 팬 OFF 90초 기록 시작.', flush=True)
                    save()
            elif command == 'removed' and timing is not None:
                try:
                    timing.confirm_removal(now)
                except ValueError as exc:
                    print(str(exc), flush=True)
                else:
                    state.update(removal_confirmed=timestamp(), phase=timing.phase)
                    event('removal_confirmed', elapsed_sec=now-started)
                    print('POST_REMOVAL: 팬 OFF 유지, 제거 후 30초 기록 시작.', flush=True)
            line = reader.read()
            now = time.monotonic()
            if timing is not None and timing.removed is not None and now-timing.removed >= 30:
                continue
            d = parse_data_line(line)
            if d:
                latest, last_data_at = d, now
                stopped = all(d[k] == 0 for k in ('duty_in','duty_out','rpm_in','rpm_out'))
                stop_count = stop_count + 1 if stopped else 0
                if not stopped:
                    raise OSError('Fans were not OFF')
                if started is not None:
                    bme_ok = d['bme_err'] == 0 and all(math.isfinite(d[k]) for k in ('temp_c','rh_pct'))
                    writer.writerow([timestamp(), d['temp_c'] if bme_ok else '',
                                     d['rh_pct'] if bme_ok else '', d['duty_out'], d['duty_in'],
                                     0, d['rpm_out'], d['rpm_in'], d['pm2_5'] if d['pms_stale']==0 else ''])
                    csvfile.flush()
                    state['rows'] += 1
            sample = parse_pms(line)
            if sample:
                previous = gate.last
                gate.accept(sample, now)
                if previous and previous['seq'] == sample['seq']:
                    continue
                state['last_sensor'] = sample
                if started is not None:
                    samples.write(json.dumps(dict(Time=timestamp(), elapsed_sec=now-started, **sample))+'\n')
                    samples.flush()
                    state['peak_pm25'] = max(state.get('peak_pm25', 0), sample['pm25'])
                if gate.ready(now) and stop_count >= 2 and camera_ready['ready'] and not ready_announced:
                    print('READY PM2.5='+str(sample['pm25'])+'; fans OFF; enter arm then cue/start', flush=True)
                    ready_announced = True
            if d or sample:
                save()
    except BaseException as exc:
        state.update(status='stopped' if isinstance(exc,(KeyboardInterrupt,InterruptedError)) else 'failed', error=str(exc))
        print('\a중단: 연기 발생원을 끄고 제거하세요. '+str(exc), flush=True)
    finally:
        if cam:
            try:
                cam.pause()
                cam.close()
            except Exception as exc:
                state.update(status='failed', error=str(exc))
        if port.is_open:
            try:
                state['stop_verified'] = stop_fans(port)
                if not state['stop_verified']:
                    state.update(status='failed', error='Final fan stop not verified')
            except Exception as exc:
                state.update(status='failed', error=str(exc), stop_verified=False)
            port.close()
        for handle in handles:
            handle.close()
        if state['status'] == 'stopping' and state['stop_verified']:
            state['status'] = 'complete'
        state['ended'] = timestamp()
        save()
        print('RESULT '+json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
