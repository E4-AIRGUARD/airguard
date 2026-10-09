"""10s baseline / smoke (--smoke-sec, default 60) / removal / 30s fans OFF / recovery to PM <=20.

Dose pilot: flag a conservative atmospheric-output experiment limit and a
suspected saturation pattern. These are protocol checks, not accuracy claims.
PEAK_TARGET (400 +/- PEAK_TOLERANCE) is provisional pending hardware trials.

No serial commands or camera in --mode monitor (default). Experiments require
the PMS_SAMPLE stream from new_fan_t.ino; legacy DATA can only be previewed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from analyze_smoke_recovery import EXPERIMENT_MAX_PM25, classify
from measure_clean_air_matrix import COMBINATIONS
from measure_thermal_response import CSV_HEADER, parse_data_line
from measure_smoke_after_manual import write_status


PEAK_TARGET = 400
PEAK_TOLERANCE = 50
PEAK_MIN = 300        # must cross 300 in recovery; a higher global peak does not prove this
MAX_SMOKE_SEC = 120
MIN_RECOVERY_SEC = 10  # allow for the sensor's total response before accepting low PM


def peak_verdict(peak, suspected_saturation, over_limit):
    if suspected_saturation:
        return 'excluded_suspected_saturation'
    if over_limit or (peak is not None and peak > EXPERIMENT_MAX_PM25):
        return 'excluded_over_experiment_limit'
    if peak is None:
        return 'no_samples'
    if peak <= PEAK_MIN:
        return 'too_low'
    if peak < PEAK_TARGET - PEAK_TOLERANCE:
        return 'below_target'
    if peak <= PEAK_TARGET + PEAK_TOLERANCE:
        return 'target'
    return 'above_target'


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def parse_pms(line):
    fields = line.strip().split(',')
    if len(fields) != 6 or fields[0] != 'PMS_SAMPLE':
        return None
    try:
        seq, uptime, pm1, pm25, pm10 = map(int, fields[1:])
    except ValueError:
        return None
    if not (1 <= seq <= 0xffffffff and 0 <= uptime <= 0xffffffff
            and all(0 <= x <= 65535 for x in (pm1, pm25, pm10))):
        return None
    return dict(seq=seq, uptime_ms=uptime, pm1=pm1, pm25=pm25, pm10=pm10)


class SmokeCycle:
    """Monotonic-clock state machine. Only unique validated PMS frames count."""
    def __init__(self, opened_at, smoke_fans_off=False, smoke_sec=60, manual_smoke=False):
        self.opened_at = opened_at
        self.smoke_fans_off = smoke_fans_off
        self.smoke_sec = smoke_sec
        self.manual_smoke = manual_smoke
        self.phase = 'monitoring'
        self.last = None
        self.last_at = None
        self.low_count = 0
        self.started_at = None
        self.peak = None
        self.peak_within_limit = None  # protocol limit does not establish accuracy
        self.suspected_saturation = False
        self.over_limit = False
        self.removed_at = None
        self.inserted_at = None
        self.off_confirmed_at = None
        self.ventilation_at = None
        self.ventilation_verified = False
        self.baseline_exceedance_reported = False

    def fresh(self, now):
        return self.last_at is not None and now - self.last_at <= 6

    def ready(self, now):
        return now - self.opened_at >= 30 and self.fresh(now) and self.low_count >= 3

    def accept(self, sample, now):
        if self.last and (sample['seq'] < self.last['seq']
                          or sample['uptime_ms'] < self.last['uptime_ms']):
            raise OSError('ESP32 restart/counter rollover detected; re-open monitor')
        if self.last and sample['seq'] == self.last['seq']:
            return []
        if not self.fresh(now):
            self.low_count = 0
        self.last, self.last_at = sample, now
        self.low_count = self.low_count + 1 if sample['pm25'] <= 20 else 0
        events = []
        if self.started_at is not None:
            self.peak = max(self.peak or 0, sample['pm25'])
            quality = classify(sample['pm1'], sample['pm25'], sample['pm10'])
            if quality == 'within_experiment_limit':
                self.peak_within_limit = max(self.peak_within_limit or 0, sample['pm25'])
            if sample['pm25'] > EXPERIMENT_MAX_PM25 and not self.over_limit:
                self.over_limit = True
                events.append('pm_over_limit')
            if quality == 'suspected_saturation' and not self.suspected_saturation:
                self.suspected_saturation = True
                events.append('pm_suspected_saturation')
        if self.phase == 'baseline' and sample['pm25'] > 20 and not self.baseline_exceedance_reported:
            self.baseline_exceedance_reported = True
            events.append('baseline_pm_above_start_limit')
        if (self.phase == 'recovery' and self.ventilation_verified
                and now - self.ventilation_at >= MIN_RECOVERY_SEC and self.low_count >= 3):
            self.phase = 'complete'
            events.append('recovery_complete')
        return events

    def tick(self, now):
        if self.phase == 'baseline' and now - self.started_at >= 10 and self.fresh(now):
            if self.smoke_fans_off:
                self.phase = 'stopping_for_smoke'
                return ['smoke_fan_stop_requested']
            self.phase = 'await_insert'
            return ['insert_requested']
        if self.phase == 'smoking' and now - self.inserted_at >= self.smoke_sec:
            self.phase = 'await_remove'
            return ['remove_requested']
        if (self.phase == 'settling' and self.off_confirmed_at is not None
                and now - self.off_confirmed_at >= 30 and self.fresh(now)):
            self.phase, self.ventilation_at, self.low_count = 'recovery', now, 0
            return ['ventilation_started']
        return []

    def confirm_fans_off(self, now):
        if self.phase == 'stopping_for_smoke':
            self.phase, self.off_confirmed_at = 'await_insert', now
            return 'insert_requested'
        if self.phase == 'stopping_for_settle':
            self.phase, self.off_confirmed_at = 'settling', now
            return 'fans_off_confirmed'
        return None

    def start(self, now):
        if self.phase != 'monitoring' or not self.ready(now):
            raise ValueError('Start requires 30s monitor warmup and 3 fresh PM2.5 <=20 frames')
        self.phase, self.started_at = 'baseline', now
        self.peak = self.peak_within_limit = self.last['pm25']

    def command(self, command, now):
        if command == 'inserted' and self.phase == 'await_insert':
            self.phase, self.inserted_at = 'smoking', now
            return 'insertion_confirmed_by_user'
        # A manually removed source must trigger ventilation even before the
        # nominal timer expires (e.g. an unexpectedly large dose).
        if command == 'removed' and (self.phase in ('smoking', 'await_remove')
                                     or (self.manual_smoke and self.phase == 'await_insert')):
            self.phase, self.removed_at, self.low_count = 'stopping_for_settle', now, 0
            if self.smoke_fans_off:
                self.phase, self.ventilation_at = 'recovery', now
            return 'removal_confirmed_by_user'
        raise ValueError('Command does not match the current phase')


class SerialLines:
    """Preserve a partial line across serial timeout boundaries."""
    def __init__(self, port):
        self.port, self.buffer = port, bytearray()

    def read(self):
        if b'\n' not in self.buffer:
            self.buffer.extend(self.port.read(max(1, self.port.in_waiting)))
        if len(self.buffer) > 65536:
            raise OSError('Serial stream has no complete lines')
        if b'\n' not in self.buffer:
            return ''
        raw, _, self.buffer = self.buffer.partition(b'\n')
        return raw.decode('utf-8', 'replace').strip()


def stop_fans(port, clock=None, timeout=10):
    """Require two stopped DATA reports, ignoring interleaved log/PMS lines."""
    clock = clock or time.monotonic
    port.reset_input_buffer()
    reader = SerialLines(port)
    port.write(b'0\n')
    until, count = clock() + timeout, 0
    while clock() < until:
        d = parse_data_line(reader.read())
        if d is None:
            continue
        count = count + 1 if all(d[k] == 0 for k in
                                ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')) else 0
        if count >= 2:
            return True
    return False


def read_commands(commands):
    for line in sys.stdin:
        commands.put(line.strip().lower())
    commands.put('quit')  # An unattended/lost control terminal must not keep a run going.


def alert(event, smoke_sec=60):
    messages = {
        'insert_requested': '정상 10초 완료. 연기를 투입하고 inserted + Enter를 입력하세요.',
        'remove_requested': f'연기 생성 {smoke_sec:g}초 종료! 발생원을 끄고 제거한 뒤 removed + Enter를 입력하세요.',
        'pm_over_limit': f'경고: PM2.5 실험 상한 {EXPERIMENT_MAX_PM25} 초과 — 본 비교에서 제외합니다. 회복까지 기록은 계속합니다.',
        'pm_suspected_saturation': '경고: 높은 PM1=PM2.5=PM10, 포화 의심 — 본 비교에서 제외합니다. 투입량을 줄이세요.',
        'fans_off_confirmed': '양쪽 팬 정지 확인. 30초 동안 정지 상태로 촬영·센서 기록을 계속합니다.',
        'ventilation_started': '팬 OFF 30초 완료. 기존 팬 조합으로 환기를 시작합니다.',
        'direct_ventilation_started': '발생원 제거 확인. 해당 팬 조합으로 즉시 환기를 시작합니다.',
    }
    print('\a' + messages.get(event, event), flush=True)


def make_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('monitor', 'experiment'), default='monitor')
    p.add_argument('--port', default='COM7')
    p.add_argument('--combination', type=int, choices=range(1, 10), default=1)
    p.add_argument('--smoke-fans-off', action='store_true',
                   help='Second experiment: fans OFF during smoke, immediate ventilation after removal')
    p.add_argument('--smoke-sec', type=float, default=60,
                   help=f'Smoke generation seconds after inserted (0 < s <= {MAX_SMOKE_SEC}, default 60)')
    p.add_argument('--manual-smoke', action='store_true',
                   help='Operator times the dose; accept removed without inserted, leaving exposure time unknown')
    p.add_argument('--camera-manual-exposure', type=int)
    p.add_argument('--start-when-ready', action='store_true',
                   help='Explicitly start experiment once fresh pre-start conditions pass')
    p.add_argument('--camera-index', type=int, default=1)
    p.add_argument('--camera-rotation', type=int, choices=(0,180), default=180)
    p.add_argument('--no-camera', action='store_true')
    p.add_argument('--no-environment', action='store_true',
                   help='Omit temperature, humidity and pressure columns from new measurement CSVs')
    p.add_argument('--max-run-sec', type=float, default=1800,
                   help='Failure timeout, not a successful measurement duration (default 1800s)')
    p.add_argument('--out-root', type=Path, default=Path(__file__).resolve().parent /
                   'data' / 'clean_air_fan_matrix' / 'smoke_cycle')
    return p


def main(argv=None):
    p = make_parser()
    args = p.parse_args(argv)
    csv_columns = [name for name in CSV_HEADER
                   if not args.no_environment or name not in ('Temp', 'Humidity', 'Pressure')]
    if not math.isfinite(args.max_run_sec) or args.max_run_sec <= 100:
        p.error('--max-run-sec must be finite and >100')
    if not math.isfinite(args.smoke_sec) or not 0 < args.smoke_sec <= MAX_SMOKE_SEC:
        p.error(f'--smoke-sec must be finite, >0 and <={MAX_SMOKE_SEC}')
    if args.camera_index < 0:
        p.error('--camera-index must be >=0')
    if args.manual_smoke and not args.smoke_fans_off:
        p.error('--manual-smoke requires --smoke-fans-off (immediate recovery)')
    import serial
    from thermal_camera import CameraRecorder
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    commands = queue.Queue()
    threading.Thread(target=read_commands, args=(commands,), daemon=True).start()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
    port.dtr = port.rts = False
    port.port = args.port
    cam = run = csvfile = eventfile = samplefile = None
    fans_commanded = False
    latest_data = None
    last_data_at = None
    arming_at = None
    expected_fans = None
    fan_changed_at = None
    off_samples = 0
    running_samples = 0
    state = dict(status='monitoring', stop_verified=False, rows=0, images=0,
                 protocol=(f'baseline10_smoke{args.smoke_sec:g}_fans_off_direct_recovery' if args.smoke_fans_off
                           else f'baseline10_smoke{args.smoke_sec:g}_off30_recovery'),
                 recovery_pm25=20, baseline_sec=10, smoke_sec=args.smoke_sec,
                 min_recovery_sec=MIN_RECOVERY_SEC,
                 dose_timing_mode='operator_manual' if args.manual_smoke else 'confirmation_timer',
                 peak_target_pm25=PEAK_TARGET, peak_tolerance_pm25=PEAK_TOLERANCE, fans_off_sec=0 if args.smoke_fans_off else 30,
                 experiment_max_pm25=EXPERIMENT_MAX_PM25, quality_policy='atmospheric_pilot_v2',
                 camera_rotation_deg=args.camera_rotation,
                 start_temperature_range=None,
                 max_run_sec=args.max_run_sec, combination=args.combination,
                 pm_output='atmospheric', error=None,
                 csv_columns=csv_columns, environment_columns_omitted=args.no_environment)

    def event(name, **details):
        record = dict(Time=timestamp(), event=name, phase=cycle.phase, **details)
        if eventfile:
            eventfile.write(json.dumps(record, ensure_ascii=False) + '\n')
            eventfile.flush()
        if name in ('insert_requested', 'remove_requested', 'fans_off_confirmed',
                    'ventilation_started', 'direct_ventilation_started',
                    'pm_over_limit', 'pm_suspected_saturation'):
            if name == 'insert_requested' and args.manual_smoke:
                print(f'\a연기를 넣으세요. 직접 {args.smoke_sec:g}초를 재어 제거한 뒤 removed + Enter.', flush=True)
            else:
                alert(name, args.smoke_sec)

    def save():
        if run:
            state.update(updated=timestamp(), phase=cycle.phase, peak_pm25=cycle.peak,
                         peak_within_limit_pm25=cycle.peak_within_limit,
                         suspected_saturation=cycle.suspected_saturation,
                         over_limit=cycle.over_limit,
                         peak_verdict=peak_verdict(cycle.peak, cycle.suspected_saturation, cycle.over_limit),
                         last_sensor=cycle.last, images=cam.count if cam else 0)
            write_status(run / 'status.json', state)

    def set_fans(pair, now):
        nonlocal expected_fans, fan_changed_at, off_samples, running_samples
        expected_fans, fan_changed_at, off_samples = pair, now, 0
        running_samples = 0
        cycle.ventilation_verified = False
        port.write(f'F {pair[0]} {pair[1]}\n'.encode())
        event('fan_command', intake=pair[0], exhaust=pair[1], elapsed_sec=now-cycle.started_at)

    try:
        port.open()
        port.reset_input_buffer()
        reader = SerialLines(port)
        cycle = SmokeCycle(time.monotonic(), smoke_fans_off=args.smoke_fans_off,
                           smoke_sec=args.smoke_sec, manual_smoke=args.manual_smoke)
        print('실시간 PM 표시 시작. 단위 µg/m³. 팬 명령 없이 모니터링합니다.', flush=True)
        print('명령: start(실험 모드만), inserted, removed, quit. 입력 후 Enter.', flush=True)
        if args.mode == 'experiment':
            print(f'설정: 조합 {args.combination}, 투입 안내까지 {args.smoke_sec:g}초, '
                  + ('투입 중 팬 OFF / 제거 후 즉시 환기' if args.smoke_fans_off else
                     '투입 중 팬 ON / 제거 후 팬 OFF 30초')
                  + f', 피크 목표 {PEAK_TARGET}±{PEAK_TOLERANCE}', flush=True)
        while True:
            now = time.monotonic()
            if run and (run / 'STOP').exists():
                raise InterruptedError('STOP file requested')
            if cam:
                cam.check()
            if fans_commanded:
                if not cycle.fresh(now):
                    raise OSError('No fresh PMS frame for 6 seconds')
                if last_data_at is None or now - last_data_at > 5:
                    raise OSError('Fan DATA missing for 5 seconds')
                if now - cycle.started_at > args.max_run_sec:
                    raise TimeoutError('Run limit reached; target/recovery not completed')
            if arming_at is not None and now - arming_at > 60:
                raise TimeoutError('Start recheck timed out (60s); fans not started')
            for transition in cycle.tick(now):
                if transition == 'remove_requested':
                    state['removal_requested'] = timestamp()
                    state['removal_prompt_lateness_sec'] = max(0, now-cycle.inserted_at-args.smoke_sec)
                if transition == 'smoke_fan_stop_requested':
                    set_fans((0,0),now)
                if transition == 'ventilation_started':
                    set_fans((intake, exhaust), now)
                    state['ventilation_started'] = timestamp()
                event(transition, elapsed_sec=now-cycle.started_at)
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            if (command is None and args.start_when_ready and args.mode == 'experiment'
                    and run is None and cycle.ready(now)):
                command = 'start'
            if command in ('quit', 'q', 'stop'):
                if run:
                    raise InterruptedError('User stopped experiment')
                break
            if command == 'start':
                if args.mode != 'experiment':
                    print('모니터 전용입니다. --mode experiment로 실행해야 합니다.', flush=True)
                elif run:
                    print('이미 시작 요청을 처리 중입니다.', flush=True)
                elif not cycle.ready(now):
                    print('대기: 30초 예열, 새 PMS 표본 3개 연속 <=20 필요. '
                          'PMS_SAMPLE이 없으면 새 펌웨어 업로드가 필요합니다.', flush=True)
                else:
                    name, intake, exhaust = COMBINATIONS[args.combination - 1]
                    run = args.out_root / datetime.now().strftime('%Y%m%d_%H%M%S_%f') / f'{args.combination:02d}_{name}'
                    run.mkdir(parents=True, exist_ok=False)
                    csvfile = (run / 'sensors.csv').open('x', encoding='utf-8', newline='')
                    writer = csv.writer(csvfile)
                    writer.writerow(csv_columns)
                    csvfile.flush()
                    eventfile = (run / 'events.jsonl').open('x', encoding='utf-8')
                    samplefile = (run / 'pms_samples.jsonl').open('x', encoding='utf-8')
                    state.update(status='arming', intake=intake, exhaust=exhaust,
                                 started=None, camera_enabled=not args.no_camera)
                    event('start_requested')
                    if not args.no_camera:
                        cam = CameraRecorder(run / 'images', index=args.camera_index, interval=1.0,
                                             rotation_deg=args.camera_rotation,
                                             manual_exposure=args.camera_manual_exposure)
                        cam.open()
                    # Recheck after potentially blocking camera setup; buffered readings
                    # must never authorize fan start or be treated as live observations.
                    port.reset_input_buffer()
                    reader.buffer.clear()
                    cycle.low_count, cycle.last_at = 0, None
                    arming_at = time.monotonic()
                    print(f'저장 위치: {run}\n카메라 준비 후 시작 조건을 재확인합니다.', flush=True)
                    save()
            elif command:
                try:
                    transition = cycle.command(command, now)
                except ValueError as exc:
                    print(str(exc), flush=True)
                else:
                    event(transition, elapsed_sec=now-cycle.started_at)
                    if command == 'removed':
                        state['removal_confirmed'] = timestamp()
                        actual = None if cycle.inserted_at is None else now-cycle.inserted_at
                        state['actual_smoke_sec'] = actual
                        state['removed_before_nominal'] = None if actual is None else actual < args.smoke_sec
                        state['removal_delay_after_nominal_sec'] = None if actual is None else max(0, actual-args.smoke_sec)
                        if actual is None:
                            event('insertion_time_unknown_operator_timed', nominal_smoke_sec=args.smoke_sec,
                                  elapsed_sec=now-cycle.started_at)
                        if args.smoke_fans_off:
                            set_fans((intake,exhaust),now)
                            state['ventilation_started'] = timestamp()
                            event('direct_ventilation_started',elapsed_sec=now-cycle.started_at)
                        else:
                            set_fans((0, 0), now)
                    elif command == 'inserted':
                        state['insertion_confirmed'] = timestamp()
            line = reader.read()
            now = time.monotonic()
            d = parse_data_line(line)
            if d:
                latest_data, last_data_at = d, now
                if not cycle.fresh(now):
                    print(f'{timestamp()} PM2.5={d["pm2_5"]} [기존 DATA/반복값 가능; '
                          f'stale={d["pms_stale"]}, 새 PMS 프레임 미확인]', flush=True)
                if fans_commanded:
                    duties_ok = (d['duty_in'], d['duty_out']) == expected_fans
                    stopped = duties_ok and d['rpm_in'] == 0 and d['rpm_out'] == 0
                    rpm_ok = stopped if expected_fans == (0, 0) else d['rpm_in'] > 0 and d['rpm_out'] > 0
                    running_samples = (running_samples + 1 if expected_fans != (0, 0)
                                       and duties_ok and rpm_ok else 0)
                    if cycle.phase == 'recovery':
                        was_verified = cycle.ventilation_verified
                        cycle.ventilation_verified = running_samples >= 2
                        if cycle.ventilation_verified and not was_verified:
                            state['ventilation_verified'] = timestamp()
                            event('ventilation_verified', elapsed_sec=now-cycle.started_at,
                                  intake_rpm=d['rpm_in'], exhaust_rpm=d['rpm_out'])
                    if ((cycle.phase == 'settling' or (args.smoke_fans_off and
                            cycle.phase in ('await_insert','smoking','await_remove'))) and not stopped) or (
                            now - fan_changed_at > 8 and not (duties_ok and rpm_ok)):
                        raise OSError('Fan duty/RPM verification failed')
                    if cycle.phase in ('stopping_for_settle','stopping_for_smoke'):
                        off_samples = off_samples + 1 if stopped else 0
                        if off_samples >= 2:
                            event(cycle.confirm_fans_off(now), elapsed_sec=now-cycle.started_at)
                            state['fans_off_confirmed'] = timestamp()
                    bme_ok = d['bme_err'] == 0 and all(math.isfinite(d[k]) for k in ('temp_c', 'rh_pct'))
                    values = [timestamp(), d['temp_c'] if bme_ok else '',
                                     d['rh_pct'] if bme_ok else '', d['duty_out'], d['duty_in'],
                                     0, d['rpm_out'], d['rpm_in'],
                                     d['pm2_5'] if d['pms_stale'] == 0 else '']
                    row = dict(zip(CSV_HEADER, values))
                    writer.writerow([row[name] for name in csv_columns])
                    csvfile.flush()
                    state['rows'] += 1
            sample = parse_pms(line)
            if sample:
                previous = cycle.last
                transitions = cycle.accept(sample, now)
                if previous and sample['seq'] == previous['seq']:
                    continue
                environment_display = ('' if args.no_environment else
                                       f'Temp={latest_data["temp_c"] if latest_data else None} ')
                print(f'{timestamp()} PM1={sample["pm1"]} PM2.5={sample["pm25"]} '
                      f'PM10={sample["pm10"]} seq={sample["seq"]} '
                      f'{environment_display}'
                      f'phase={cycle.phase} 시작조건={cycle.ready(now)}', flush=True)
                if samplefile:
                    samplefile.write(json.dumps(dict(Time=timestamp(), phase=cycle.phase,
                        elapsed_sec=None if cycle.started_at is None else now-cycle.started_at,
                        **sample), ensure_ascii=False) + '\n')
                    samplefile.flush()
                for transition in transitions:
                    event(transition, pm25=sample['pm25'], elapsed_sec=now-cycle.started_at)
                if (arming_at is not None and cycle.ready(now)
                        and last_data_at is not None and now - last_data_at <= 5):
                    cycle.start(now)
                    fans_commanded = True  # Also stop on a partially failed write.
                    set_fans((intake, exhaust), now)
                    if cam:
                        cam.begin()
                    arming_at = None
                    state.update(status='running', started=timestamp())
                    state['pre_start_sensor'] = dict(temperature=latest_data['temp_c'] if latest_data else None,
                                                     pm25=sample['pm25'], Time=timestamp())
                    if args.no_environment:
                        state['pre_start_sensor'].pop('temperature')
                    event('baseline_started')
                    print('측정 시작. 팬을 유지하며 정상 10초 기록 중입니다.', flush=True)
                if cycle.phase == 'complete':
                    state.update(status='stopping', recovered_at=timestamp(),
                                 elapsed_sec=now-cycle.started_at,
                                 removal_to_recovery_sec=now-cycle.removed_at,
                                 recovery_elapsed_sec=now-cycle.ventilation_at,
                                 actual_smoke_sec=(None if cycle.inserted_at is None else
                                                   cycle.removed_at-cycle.inserted_at),
                                 actual_fans_off_sec=cycle.ventilation_at-cycle.off_confirmed_at)
                    event('measurement_ended')
                    if cam:
                        cam.pause()
                    break
            elif not line and not cycle.fresh(now):
                # Periodic no-data notice without printing on each 0.1s timeout.
                if int(now) != int(now - 0.1) and int(now) % 5 == 0:
                    print('새 PMS 수신 대기 중 — 연결/펌웨어를 확인하세요.', flush=True)
            if run and (d or sample):
                save()
    except BaseException as exc:
        state.update(status='stopped' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                     error=str(exc) or type(exc).__name__)
        print('중단: 연기 발생원을 끄고 제거하세요. ' + state['error'], flush=True)
    finally:
        # Stop fans before any slow camera teardown or file operations.
        if fans_commanded and port.is_open:
            try:
                state['stop_verified'] = stop_fans(port)
                if not state['stop_verified']:
                    raise OSError('Fan stop not verified; inspect hardware')
            except Exception as exc:
                state.update(status='failed', error=str(exc))
        if cam:
            try:
                cam.pause()
                cam.close()
            except Exception as exc:
                state.update(status='failed', error=str(exc))
        if port.is_open:
            port.close()
        if state['status'] == 'stopping' and state['stop_verified']:
            state['status'] = 'complete'
        try:
            if run:
                state['ended'] = timestamp()
                event('finished', status=state['status'], stop_verified=state['stop_verified'], error=state['error'])
                save()
        except Exception as exc:
            state.update(status='failed', error=f'Final log/status publication failed: {exc}')
        finally:
            for handle in (csvfile, eventfile, samplefile):
                if handle:
                    handle.close()
        if run:
            if state.get('peak_verdict'):
                smoke_sec = state.get('actual_smoke_sec')
                print(f"피크 판정: {state['peak_verdict']} (PM2.5 관측 최고 {state['peak_pm25']}, "
                      f"목표 {PEAK_TARGET}±{PEAK_TOLERANCE}, 실제 투입 "
                      f"{'-' if smoke_sec is None else f'{smoke_sec:.1f}'}초)", flush=True)
            print('RESULT ' + json.dumps(state, ensure_ascii=False), flush=True)
    return 1 if state['status'] in ('failed', 'stopped') else 0


if __name__ == '__main__':
    raise SystemExit(main())
