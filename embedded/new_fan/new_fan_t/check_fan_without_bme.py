"""Live PMS5003 + fan diagnostic; requires ENABLE_BME280=0 firmware.

Resets the ESP32, verifies the disabled-BME banner, runs both fans at
35/60/100 percent for ten seconds each, then verifies they stopped.
Writes diagnostic JSON only, never thermal experiment CSV or camera images.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from measure_thermal_response import parse_data_line


def main():
    root = Path(__file__).resolve().parent / 'data/fan_checks'
    root.mkdir(parents=True, exist_ok=True)
    output = root / datetime.now().strftime('without_bme_%Y%m%d_%H%M%S_%f.json')
    result = dict(started=datetime.now().isoformat(), bme_enabled=False,
                  status='running', stages=[], stop_verified=False, error=None)
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
    port.dtr = port.rts = False
    port.port = 'COM7'

    def save():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    def read():
        line = port.readline().decode('utf-8', 'replace').strip()
        sample = parse_data_line(line)
        if sample:
            return {k: sample[k] for k in
                    ('duty_in', 'rpm_in', 'duty_out', 'rpm_out', 'pm2_5', 'pms_stale')}
        return None

    def stopped(d):
        return d and all(d[k] == 0 for k in ('duty_in', 'rpm_in', 'duty_out', 'rpm_out'))

    try:
        port.open()
        port.reset_input_buffer()
        port.rts = True
        time.sleep(0.15)
        port.rts = False
        deadline = time.monotonic() + 5
        banner = ''
        while time.monotonic() < deadline:
            line = port.readline().decode('utf-8', 'replace').strip()
            if '진단용 비활성화' in line:
                banner = line
                break
        if not banner:
            raise RuntimeError('BME-disabled firmware banner not received; no fan test started')
        result['boot_banner'] = banner
        port.write(b'0\n')
        print('BME280 disabled; baseline check', flush=True)
        for label, duty, duration in [('stopped', 0, 10), ('weak', 35, 10),
                                      ('medium', 60, 10), ('strong', 100, 10)]:
            samples = []
            stage = dict(name=label, duty=duty, duration_sec=duration, samples=samples)
            result['stages'].append(stage)
            port.write(f'F {duty} {duty}\n'.encode())
            start = last = time.monotonic()
            print(f'STAGE {label}: intake/exhaust={duty}/{duty}', flush=True)
            while time.monotonic() - start < duration:
                if (root / 'STOP').exists():
                    raise InterruptedError('User requested STOP')
                d = read()
                now = time.monotonic()
                if now - start >= duration:
                    break
                if d:
                    last = now
                    d.update(Time=datetime.now().isoformat(), elapsed_sec=round(now - start, 3))
                    samples.append(d)
                elif now - last > 5:
                    raise RuntimeError('No sensor DATA for 5 seconds')
            stable = samples[-3:]
            if len(stable) < 3:
                raise RuntimeError('Not enough sensor samples')
            stage['passed'] = all(d['duty_in'] == duty and d['duty_out'] == duty
                                  and d['pms_stale'] == 0
                                  and ((d['rpm_in'] > 0 and d['rpm_out'] > 0) if duty else stopped(d))
                                  for d in stable)
            stage['last'] = stable[-1]
            save()
            print(json.dumps({k: v for k, v in stage.items() if k != 'samples'}), flush=True)
            if not stage['passed']:
                raise RuntimeError(f'{label}: fan response or PMS freshness check failed')
        result['status'] = 'complete'
    except BaseException as exc:
        result.update(status='stopped' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                      error=str(exc))
    finally:
        if port.is_open:
            try:
                port.write(b'0\n')
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    d = read()
                    if stopped(d):
                        result.update(stop_verified=True, stop_sample=d)
                        break
                if not result['stop_verified']:
                    result.update(status='failed', error=(result['error'] or '') + '; fan stop not verified')
            except Exception as exc:
                result.update(status='failed', error=(result['error'] or '') + '; stop: ' + str(exc))
            finally:
                port.close()
        result['ended'] = datetime.now().isoformat()
        save()
        print('REPORT_PATH=' + str(output), flush=True)
        print('RESULT=' + json.dumps({k: v for k, v in result.items() if k != 'stages'}, ensure_ascii=False), flush=True)
    if result['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
