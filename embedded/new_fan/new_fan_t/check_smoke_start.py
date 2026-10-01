"""Read-only 5-second PM2.5 baseline check; no fan commands, reset or camera."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from measure_thermal_response import parse_data_line
from measure_smoke_after_manual import valid_pm


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True, type=Path)
    parser.add_argument('--combination', required=True, type=int, choices=range(1, 10))
    args = parser.parse_args(argv)
    directory = args.session_dir / 'start_checks'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / datetime.now().strftime(f'{args.combination:02d}_%Y%m%d_%H%M%S_%f.json')
    report = dict(combination=args.combination, bme_enabled=False, ready=False,
                  baseline_pm25_range=[0, 20], samples=[], error=None, last=None)
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1)
    port.dtr = port.rts = False
    port.port = 'COM7'
    try:
        port.open()
        port.reset_input_buffer()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = port.readline().decode('utf-8', 'replace').strip()
            d = parse_data_line(line)
            if d:
                for key in ('temp_c', 'rh_pct', 'bme_err'):
                    d.pop(key, None)
                d['Time'] = datetime.now().astimezone().isoformat()
                report['samples'].append(d)
        report['last'] = report['samples'][-1] if report['samples'] else None
        last = report['last']
        # An old buffered sample cannot authorize a new measurement.
        fresh = last and (datetime.now().astimezone() - datetime.fromisoformat(last['Time'])).total_seconds() <= 2
        report['ready'] = bool(fresh and valid_pm(last) and 0 <= last['pm2_5'] <= 20)
        report['fans_stopped'] = bool(last and all(last[k] == 0 for k in
                                                  ('duty_in', 'duty_out', 'rpm_in', 'rpm_out')))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if port.is_open:
            port.close()
        report['checked_at'] = datetime.now().astimezone().isoformat()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REPORT_PATH=' + str(path), flush=True)
        print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False), flush=True)
    if report['error']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
