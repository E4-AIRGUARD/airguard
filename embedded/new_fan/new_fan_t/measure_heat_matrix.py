"""Manual 5-second heat pulse, then 300-second cooling for all nine fan pairs.

Camera records only during 300-second fan cooling, excluding heating and waits. Start requires a fresh, healthy
22..26 C sample and stopped fans; PM2.5 is recorded without a concentration gate.
The on-screen heat signal is a manual instruction, not heater actuation feedback.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path

from measure_clean_air_matrix import COMBINATIONS
from measure_thermal_response import CSV_HEADER, parse_data_line
from thermal_camera import CameraRecorder

ROOT = Path(__file__).resolve().parent / 'data' / 'clean_air_fan_matrix' / 'heat'
LABELS = {35: '약', 60: '중', 100: '강'}


def stamp():
    return datetime.now().isoformat(timespec='milliseconds')


def ready(sample, age):
    return bool(sample and 0 <= age <= 2.5 and sample['bme_err'] == 0
                and math.isfinite(sample['temp_c'])
                and 22 <= sample['temp_c'] <= 26
                and all(sample[k] == 0 for k in ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')))


class HeatSession:
    """Clock-driven controller; serial/camera/UI are injected by the entry point."""

    def __init__(self, directory, send, clock=time.monotonic, alert=lambda: None,
                 begin_capture=lambda: None, end_capture=lambda: None):
        self.directory = Path(directory)
        (self.directory / 'csv').mkdir(parents=True, exist_ok=True)
        self.send, self.clock, self.alert = send, clock, alert
        self.begin_capture, self.end_capture = begin_capture, end_capture
        self.stage = 'waiting'
        self.index = 0
        self.sample = None
        self.sample_at = -float('inf')
        self.deadline = 0.0
        self.completed = []
        self.rows = 0
        self.file = self.writer = None
        self.error = None
        self.event('session_started', duration_sec=300, heat_sec=5,
                   start_temperature=[22, 26], pm25_gate=False, capture_during_wait=False,
                   capture_during_heat=False,
                   heat_load_source='scheduled manual heater signal; not measured heater power')

    def event(self, kind, **values):
        with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'Time': stamp(), 'event': kind, 'combination': min(self.index + 1, 9),
                                **values}, ensure_ascii=False) + '\n')
        print(f'[{kind}] {self.index + 1}/9 {values}', flush=True)

    def can_start(self):
        return self.stage == 'waiting' and ready(self.sample, self.clock() - self.sample_at)

    def start(self):
        if not self.can_start():
            return False
        self.stage = 'countdown'
        self.deadline = self.clock() + 5
        self.event('countdown_started')
        return True

    def receive(self, sample):
        now = self.clock()
        self.sample, self.sample_at = sample, now
        if self.stage in ('heating', 'cooling'):
            if sample['bme_err'] or not math.isfinite(sample['temp_c']):
                raise OSError('측정 중 온도 센서 오류')
            self.writer.writerow([stamp(), sample['temp_c'], sample['rh_pct'] if math.isfinite(sample['rh_pct']) else '',
                                  sample['duty_out'], sample['duty_in'], int(self.stage == 'heating'),
                                  sample['rpm_out'], sample['rpm_in'], sample['pm2_5'] if not sample['pms_stale'] else ''])
            self.file.flush()
            self.rows += 1
            if self.stage == 'cooling' and now - self.cooling_started > 8:
                _, intake, exhaust = COMBINATIONS[self.index]
                if (sample['duty_in'], sample['duty_out']) != (intake, exhaust):
                    raise OSError('지정한 팬 PWM 응답이 확인되지 않음')
                if sample['rpm_in'] <= 0 or sample['rpm_out'] <= 0:
                    raise OSError('팬 가동 중 RPM=0: 팬 또는 회전수 배선을 확인하세요')
        if self.stage == 'stopping' and all(sample[k] == 0 for k in ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')):
            self.completed.append(COMBINATIONS[self.index][0])
            self.event('combination_complete', rows=self.rows, stop_verified=True)
            self.alert()
            self.index += 1
            self.stage = 'complete' if self.index == len(COMBINATIONS) else 'waiting'

    def tick(self):
        now = self.clock()
        if self.stage in ('heating', 'cooling', 'countdown') and now - self.sample_at > 5:
            raise OSError('5초 이상 유효한 센서 DATA를 받지 못했습니다')
        if self.stage == 'countdown' and now >= self.deadline:
            if not ready(self.sample, now - self.sample_at):
                self.stage = 'waiting'
                self.event('countdown_cancelled', reason='시작 온도 또는 센서/팬 상태 미충족')
                return
            name, _, _ = COMBINATIONS[self.index]
            self.file = (self.directory / 'csv' / f'{self.index + 1:02d}_{name}.csv').open('x', newline='', encoding='utf-8')
            self.writer = csv.writer(self.file)
            self.writer.writerow(CSV_HEADER)
            self.file.flush()
            self.rows = 0
            self.event('heat_start', temp_c=self.sample['temp_c'], pm2_5=self.sample['pm2_5'],
                       csv=str(self.file.name))
            self.stage = 'heating'
            self.deadline = now + 5
            self.alert()
        elif self.stage == 'heating' and now >= self.deadline:
            _, intake, exhaust = COMBINATIONS[self.index]
            self.send(f'F {intake} {exhaust}')
            self.stage = 'cooling'
            self.cooling_started = now
            self.deadline = now + 300
            self.begin_capture()
            self.event('heat_end_fans_start', intake=intake, exhaust=exhaust)
            self.alert()
        elif self.stage == 'cooling' and now >= self.deadline:
            self.end_capture()
            self.send('0')
            self.close_csv()
            self.stage = 'stopping'
            self.deadline = now + 10
            self.event('measurement_end', rows=self.rows)
        elif self.stage == 'stopping' and now >= self.deadline:
            raise OSError('정지 명령 후 10초 안에 PWM/RPM 0을 확인하지 못했습니다')

    def close_csv(self):
        if self.file:
            self.file.close()
            self.file = self.writer = None

    def snapshot(self, images=0):
        return {'updated': stamp(), 'status': self.stage, 'current': min(self.index + 1, 9),
                'completed': self.completed, 'sample': self.sample,
                'sample_age_sec': round(self.clock() - self.sample_at, 2) if self.sample else None,
                'can_start': self.can_start(), 'rows': self.rows, 'images': images,
                'remaining_sec': max(0, math.ceil(self.deadline - self.clock())), 'error': self.error}


def main():
    import serial
    import tkinter as tk

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', default='COM7')
    p.add_argument('--camera-index', type=int, default=1)
    p.add_argument('--session-dir', type=Path)
    args = p.parse_args()
    directory = args.session_dir or ROOT / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True, exist_ok=False)
    print(f'[heat] Session: {directory.resolve()}', flush=True)
    ser = None
    camera = None
    session = None
    window = None
    closed = False
    buffer = bytearray()
    last_save = 0.0
    opened = time.monotonic()

    def save():
        state = session.snapshot(camera.count if camera else 0)
        temp = directory / 'session.tmp'
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(directory / 'session.json')

    def close():
        nonlocal closed
        if closed:
            return
        closed = True
        if ser and ser.is_open:
            try:
                ser.write(b'0\n')
                ser.flush()
            except Exception as exc:
                if session:
                    session.error = f'팬 정지 명령 실패: {exc}'
                    session.stage = 'failed'
            finally:
                ser.close()
        if session:
            session.close_csv()
            if session.stage not in ('complete', 'failed'):
                session.stage = 'stopped'
        if camera:
            try:
                camera.close()
            except Exception as exc:
                if session:
                    session.error = str(exc)
                    session.stage = 'failed'
        if session:
            save()
            session.event('session_closed', status=session.stage, error=session.error)

    try:
        camera = CameraRecorder(directory / 'images', args.camera_index, 1.0)
        camera.open()
        ser = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=1)
        ser.dtr = ser.rts = False
        ser.port = args.port
        ser.open()
        opened = time.monotonic()
        ser.reset_input_buffer()
        ser.write(b'0\n')
        window = tk.Tk()
        window.title('열 실험 — 팬 9개 조합')
        window.geometry('760x470')
        window.configure(bg='#14232f')
        title = tk.Label(window, font=('Malgun Gothic', 22, 'bold'), bg='#14232f', fg='white')
        title.pack(pady=(22, 10))
        phase = tk.Label(window, font=('Malgun Gothic', 24, 'bold'), bg='#14232f', fg='#67dfbe')
        phase.pack(pady=10)
        values = tk.Label(window, font=('Malgun Gothic', 14), bg='#14232f', fg='white')
        values.pack(pady=10)
        detail = tk.Label(window, font=('Malgun Gothic', 12), bg='#14232f', fg='#bfceda', wraplength=710)
        detail.pack(pady=10)
        session = HeatSession(directory, lambda cmd: ser.write((cmd + '\n').encode()), alert=window.bell,
                              begin_capture=lambda: camera.begin() if camera.count == 0 else camera.resume(),
                              end_capture=camera.pause)
        start = tk.Button(window, text='준비 완료 — 5초 뒤 가열 시작', font=('Malgun Gothic', 15),
                          command=session.start, padx=16, pady=8)
        start.pack(pady=8)

        def quit_window():
            close()
            window.destroy()

        tk.Button(window, text='전체 중단 / 종료', command=quit_window).pack(pady=8)
        window.protocol('WM_DELETE_WINDOW', quit_window)

        def update():
            nonlocal buffer, last_save
            if closed:
                return
            try:
                if (directory / 'STOP').exists():
                    quit_window()
                    return
                camera.check()
                buffer.extend(ser.read(ser.in_waiting))
                if len(buffer) > 65536:
                    raise OSError('시리얼 버퍼 초과')
                session.tick()
                while b'\n' in buffer:
                    line, _, buffer = buffer.partition(b'\n')
                    sample = parse_data_line(line.decode('utf-8', 'replace').strip())
                    if sample:
                        session.receive(sample)
                if time.monotonic() - max(opened, session.sample_at) > 10:
                    raise OSError('센서 DATA가 10초 이상 없습니다')
                # Optional agent control: each combination has a unique, one-shot start file.
                control = directory / f'START_{session.index + 1:02d}'
                if session.can_start() and control.exists():
                    control.rename(control.with_suffix('.accepted'))
                    session.start()
                n = min(session.index, 8)
                _, intake, exhaust = COMBINATIONS[n]
                title.config(text=f'{n + 1}/9   흡기 {LABELS[intake]}({intake}%) + 배기 {LABELS[exhaust]}({exhaust}%)')
                remaining = max(0, math.ceil(session.deadline - time.monotonic()))
                messages = {'waiting': (f'{len(session.completed)}번 측정 완료 · 식힌 뒤 준비' if session.completed
                                         else '온도 확인 후 준비 버튼을 눌러주세요'),
                            'countdown': f'가열 준비… {remaining}초 뒤 시작',
                            'heating': f'지금 드라이기 ON — {remaining}초 남음',
                            'cooling': f'드라이기 OFF · 팬 측정 {remaining}초 남음',
                            'stopping': '300초 종료 · 팬 정지 확인 중',
                            'complete': '9개 조합 측정 완료'}
                phase.config(text=messages.get(session.stage, session.stage),
                             fg='#ffb16b' if session.stage == 'heating' else '#67dfbe')
                d = session.sample
                values.config(text=(f"온도 {d['temp_c']:.2f}℃   습도 {d['rh_pct']:.1f}%   PM2.5 {d['pm2_5']}\n"
                                    f"흡기 {d['rpm_in']} RPM   배기 {d['rpm_out']} RPM" if d else '센서 수신 대기'))
                detail.config(text=f'완료 {len(session.completed)}/9 · 사진 {camera.count}장 (가열 후 팬 측정 중만 1초 간격)\n'
                              '시작 조건: 온도 22~26℃ + 센서 정상 + 팬 정지\n'
                              '가열 시작/종료는 화면 신호에 맞춰 직접 조작하세요.')
                start.config(state='normal' if session.can_start() else 'disabled')
                if time.monotonic() - last_save > 1:
                    save()
                    last_save = time.monotonic()
                if session.stage == 'complete':
                    close()
                    return
                window.after(50, update)
            except Exception as exc:
                session.error = str(exc)
                session.stage = 'failed'
                phase.config(text='오류 — 드라이기를 끄세요', fg='#ff827b')
                detail.config(text=str(exc))
                start.config(state='disabled')
                window.bell()
                close()

        window.after(50, update)
        window.mainloop()
    finally:
        close()


if __name__ == '__main__':
    main()
