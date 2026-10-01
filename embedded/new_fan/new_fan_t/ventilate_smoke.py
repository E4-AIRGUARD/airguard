"""Explicitly requested ventilation, both fans 100%, stop at fresh PM2.5 <=20."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from measure_thermal_response import parse_data_line
from measure_smoke_after_manual import valid_pm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True, type=Path)
    args = parser.parse_args()
    report = dict(status='starting', started=datetime.now().astimezone().isoformat(),
                  target_pm25_max=20, bme_enabled=False, stop_verified=False, samples=[], error=None)
    directory = args.session_dir / 'ventilation'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / datetime.now().strftime('%Y%m%d_%H%M%S.json')
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
    port.dtr = port.rts = False
    port.port = 'COM7'

    def read():
        d = parse_data_line(port.readline().decode('utf-8', 'replace').strip())
        if d:
            for key in ('temp_c', 'rh_pct', 'bme_err'):
                d.pop(key, None)
            d['Time'] = datetime.now().astimezone().isoformat()
        return d

    try:
        if (args.session_dir / 'STOP').exists():
            raise InterruptedError('STOP requested')
        port.open()
        port.reset_input_buffer()
        deadline = time.monotonic() + 5
        first = None
        while time.monotonic() < deadline:
            first = read()
            if valid_pm(first):
                break
        if not valid_pm(first):
            raise OSError('Fresh PMS5003 reading unavailable')
        report['samples'].append(first)
        if first['pm2_5'] <= 20:
            report['status'] = 'target_reached'
        else:
            port.write(b'F 100 100\n')
            start = last = time.monotonic()
            next_print = start
            report['status'] = 'ventilating'
            print('FANS_REQUESTED=100/100 PM25=' + str(first['pm2_5']), flush=True)
            while time.monotonic() - start < 900:
                if (args.session_dir / 'STOP').exists():
                    raise InterruptedError('STOP requested')
                d = read()
                now = time.monotonic()
                if d is None:
                    if now - last > 5:
                        raise OSError('No sensor DATA for 5 seconds')
                    continue
                last = now
                if not valid_pm(d):
                    raise OSError('PMS5003 stale or invalid')
                report['samples'].append(d)
                if now >= next_print:
                    print(json.dumps(d), flush=True)
                    next_print = now + 30
                if d['pm2_5'] <= 20:
                    report['status'] = 'target_reached'
                    break
                if now - start > 8 and ((d['duty_in'], d['duty_out']) != (100, 100)
                                        or d['rpm_in'] <= 0 or d['rpm_out'] <= 0):
                    raise OSError('Fan duty/RPM verification failed')
            else:
                report['status'] = 'timeout'
    except BaseException as exc:
        report.update(status='stopped' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                      error=str(exc))
    finally:
        if port.is_open:
            try:
                port.write(b'0\n')
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    d = read()
                    if d and all(d[k] == 0 for k in ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')):
                        report.update(stop_verified=True, stop_sample=d)
                        break
                if not report['stop_verified']:
                    report.update(status='failed', error='Fan stop not verified')
            except Exception as exc:
                report.update(status='failed', error=str(exc))
            finally:
                port.close()
        report['ended'] = datetime.now().astimezone().isoformat()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REPORT_PATH=' + str(path.resolve()), flush=True)
        print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False), flush=True)
    if report['status'] != 'target_reached':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
