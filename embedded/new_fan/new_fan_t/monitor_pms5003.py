"""PMS5003 live window; optional explicitly requested fan duty. No recording."""
import argparse
from collections import deque
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports
from measure_smoke_cycle import parse_pms
from measure_thermal_response import parse_data_line


def complete_lines(port, stopped):
    """Keep partial UART lines across read timeouts."""
    buffer = bytearray()
    while not stopped.is_set():
        buffer.extend(port.read(max(1, port.in_waiting)))
        while b'\n' in buffer:
            raw, _, buffer = buffer.partition(b'\n')
            yield raw.decode('utf-8', 'replace').strip()
        if len(buffer) > 65536:
            raise OSError('Serial stream has no complete lines')
        yield ''  # Allow communication watchdogs to run during receive timeouts.


def receive(port_name, stopped, updates, fan_duty=None):
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
    port.dtr = port.rts = False
    port.port = port_name
    last_seq = None
    last_fresh_at = None
    announced = False
    fan_started = None
    last_data_at = None
    fan_verified = False
    def send(value):
        try:
            updates.put_nowait(value)
        except queue.Full:
            updates.get_nowait()
            updates.put_nowait(value)
    try:
        port.open()
        port.reset_input_buffer()
        for line in complete_lines(port, stopped):
            now = time.monotonic()
            if fan_started is not None:
                if now-last_fresh_at > 6 or now-(last_data_at or fan_started) > 5:
                    raise OSError('팬 정지: 센서 통신 시간 초과')
            pm = parse_pms(line)
            if pm:
                if pm['seq'] == last_seq:
                    continue
                last_seq, last_fresh_at = pm['seq'], now
                send(('sample', now, pm['pm25'], f'새 PMS 프레임 #{last_seq}', False))
                if not announced:
                    print(f'LIVE_READY port={port_name} PM2.5={pm["pm25"]} seq={last_seq}', flush=True)
                    announced = True
                if fan_duty is not None and fan_started is None:
                    fan_started = now
                    port.write(f'F {fan_duty} {fan_duty}\n'.encode())
                    send(('fan', f'흡기/배기 {fan_duty}% 가동 요청 — 회전 확인 중'))
            else:
                data = parse_data_line(line)
                if data:
                    last_data_at = now
                    if fan_started is not None:
                        good = ((data['duty_in'],data['duty_out']) == (fan_duty,fan_duty)
                                and data['rpm_in'] > 0 and data['rpm_out'] > 0)
                        if now-fan_started > 8 and not good:
                            raise OSError('팬 정지: PWM/RPM 확인 실패')
                        message = (f'흡기 {data["duty_in"]}% / {data["rpm_in"]} RPM · '
                                   f'배기 {data["duty_out"]}% / {data["rpm_out"]} RPM')
                        send(('fan', message))
                        if good and now-fan_started >= 3 and not fan_verified:
                            print('FAN_VERIFIED '+message, flush=True)
                            fan_verified = True
                    if last_fresh_at is None or now-last_fresh_at > 6:
                        send(('sample', now, data['pm2_5'],
                              '기존 DATA 출력 — 같은 PMS 값이 반복 표시될 수 있음',
                              data['pms_stale'] != 0))
    except Exception as exc:
        print(f'MONITOR_ERROR: {exc}', flush=True)
        send(('error', str(exc)))
    finally:
        if port.is_open:
            try:
                if fan_started is not None:
                    port.write(b'0\n')
                    end, count, buffer = time.monotonic()+10, 0, bytearray()
                    while time.monotonic() < end and count < 2:
                        buffer.extend(port.read(max(1, port.in_waiting)))
                        while b'\n' in buffer:
                            raw, _, buffer = buffer.partition(b'\n')
                            data = parse_data_line(raw.decode('utf-8','replace').strip())
                            if data:
                                count = count+1 if all(data[k]==0 for k in
                                    ('duty_in','duty_out','rpm_in','rpm_out')) else 0
                                if count >= 2:
                                    break
                    if count < 2:
                        raise OSError('팬 정지 확인 실패: 장비를 확인하세요')
                    print('FANS_STOPPED_VERIFIED', flush=True)
                    send(('fan', '양쪽 팬 정지 확인'))
            except Exception as exc:
                print(f'FAN_STOP_ERROR: {exc}', flush=True)
                send(('error', str(exc)))
            finally:
                port.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM7')
    parser.add_argument('--fan-duty', type=int, choices=(35,60,100),
                        help='Explicit fan control; stop both fans when window closes')
    args = parser.parse_args(argv)
    root = tk.Tk()
    root.title('PMS5003 실시간 확인 — 실험 시작 전')
    root.geometry('780x490')
    panel = ttk.Frame(root, padding=18)
    panel.pack(fill='both', expand=True)
    ports = ', '.join(p.device for p in list_ports.comports()) or '없음'
    ttk.Label(panel, text=f'연결 대상 {args.port} | 감지 포트: {ports}').pack(anchor='w')
    fan_text = tk.StringVar(value='팬 제어 없음' if args.fan_duty is None else
                           f'흡기/배기 {args.fan_duty}% 준비 · 창을 닫으면 팬 정지')
    ttk.Label(panel, textvariable=fan_text).pack(anchor='w', pady=5)
    ttk.Label(panel, text='사진/실험 데이터 저장 없음 · 목표값 미설정').pack(anchor='w')
    value = tk.StringVar(value='수신 대기')
    ttk.Label(panel, textvariable=value, font=('맑은 고딕', 28, 'bold')).pack(anchor='w', pady=12)
    status = tk.StringVar(value='ESP32 연결을 확인하고 값을 기다립니다.')
    ttk.Label(panel, textvariable=status, wraplength=730).pack(anchor='w')
    canvas = tk.Canvas(panel, height=220, background='#f4f7fa', highlightthickness=0)
    canvas.pack(fill='both', expand=True, pady=12)
    ttk.Label(panel, text='최근 120초 PM2.5 표시값 / µg/m³ · 자동 축척 · 정확도 보정값 아님').pack(anchor='w')
    stopped, updates = threading.Event(), queue.Queue(maxsize=1000)
    history = deque(maxlen=2000)
    last = None
    error = None
    worker = threading.Thread(target=receive, args=(args.port, stopped, updates, args.fan_duty), daemon=True)
    worker.start()

    def close():
        stopped.set()
        status.set('연결 종료 및 팬 정지 확인 중…' if args.fan_duty is not None else '연결 종료 중…')
        if worker.is_alive():
            root.after(100, close)
        else:
            root.destroy()

    def refresh():
        nonlocal last, error
        while True:
            try:
                item = updates.get_nowait()
            except queue.Empty:
                break
            if item[0] == 'error':
                error = item[1]
                continue
            if item[0] == 'fan':
                fan_text.set(item[1])
                continue
            _, at, pm25, source, stale = item
            last = (at, pm25, source, stale)
            if not stale:
                history.append((at, pm25))
        now = time.monotonic()
        if error:
            value.set('연결 오류')
            status.set(error + ' | 연결 후 창을 닫고 다시 실행하세요.')
        elif last:
            at, pm25, source, stale = last
            age = now-at
            value.set(f'{pm25:,} µg/m³' + (' (과거값)' if stale or age > 6 else ''))
            status.set(f'{source} | 수신 후 {age:.1f}초' +
                       (' | 통신/센서 확인 필요' if stale or age > 6 else ''))
        while history and now-history[0][0] > 120:
            history.popleft()
        canvas.delete('all')
        w, h = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 80)
        top = max(25, max((v for _, v in history), default=0)*1.15)
        canvas.create_text(5, 10, anchor='w', text=f'{top:.0f}')
        canvas.create_text(5, h-12, anchor='w', text='0')
        y20 = h-20-20/top*(h-40)
        canvas.create_line(40, y20, w-10, y20, fill='#869596', dash=(4, 4))
        canvas.create_text(w-14, y20-10, anchor='e', text='회복 기준 20')
        coords = []
        for at, pm25 in history:
            coords.extend((40+(120-(now-at))/120*(w-50), h-20-pm25/top*(h-40)))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill='#176fa7', width=2)
        root.after(200, refresh)

    root.protocol('WM_DELETE_WINDOW', close)
    ttk.Button(panel, text='종료 / 시리얼 연결 해제', command=close).pack(anchor='e', pady=5)
    refresh()
    root.mainloop()


if __name__ == '__main__':
    main()
