"""Report exploratory smoke-decay fits and separate protocol eligibility.

Equal high PM channels suggest clipping/saturation; they do not prove its cause.
500 is a conservative atmospheric-output experiment limit, NOT a calibrated
accuracy bound: the manufacturer's 0-500 specification is for CF=1 output.
Raw ln(C) fits give apparent tau. Optional ln(C-background) fits assume a known,
constant asymptote; never infer room air from the threshold-selected final samples.
R2 is in log space and does not establish mixing, calibration or independence.

Usage: python analyze_smoke_recovery.py [run_root] [--background-pm25 VALUE]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(__file__).parent / 'data' / 'clean_air_fan_matrix' / 'smoke_fan_recovery'
SUSPECT_HIGH_PM25 = 2000     # observed pattern heuristic, not a manufacturer flag
EXPERIMENT_MAX_PM25 = 500    # conservative protocol choice, atmospheric output
THRESHOLDS = (300, 150, 100, 50, 20)
PRIMARY_WINDOW = (300, 150)
SECONDARY_WINDOW = (300, 100)
FIT_MIN_POINTS = 10
FIT_MIN_DURATION_SEC = 10   # instrument total response can take up to 10 seconds
MAX_SAMPLE_GAP_SEC = 6      # matches acquisition freshness guard
METRIC_MIN_R2 = 0.99
T0_EVENTS = ('direct_ventilation_started', 'ventilation_started')
SUMMARY_NAME = 'recovery.csv'


def classify(pm1: int, pm25: int, pm10: int) -> str:
    if pm1 == pm25 == pm10 and pm25 >= SUSPECT_HIGH_PM25:
        return 'suspected_saturation'
    if pm25 > EXPERIMENT_MAX_PM25:
        return 'over_experiment_limit'
    return 'within_experiment_limit'


def settle_time(rows: list[dict], threshold: int) -> float | None:
    """Time of the first sample from which PM2.5 never again exceeds threshold."""
    settled = None
    for r in rows:
        if r['pm25'] > threshold:
            settled = None
        elif settled is None:
            settled = r['t']
    return settled


def fit_window(rows: list[dict], hi: int, lo: int, background: float = 0) -> dict | None:
    """ln(PM2.5) fit between the settle times of hi and lo.

    Returns tau (s), r2, duration (s) and point count, or None when PM2.5 never
    came down through hi, never reached lo, or the window holds too few points.
    """
    if not math.isfinite(background) or not 0 <= background < lo:
        raise ValueError('background must be finite and 0 <= background < window lower bound')
    if not rows or any(not math.isfinite(r['t']) or not math.isfinite(r['pm25'])
                       or r['pm25'] < 0 for r in rows):
        return None
    if any(b['t'] <= a['t'] for a, b in zip(rows, rows[1:])):
        return None
    if not any(r['pm25'] > hi for r in rows):
        return None
    start, end = settle_time(rows, hi), settle_time(rows, lo)
    if start is None or end is None or end - start < FIT_MIN_DURATION_SEC:
        return None
    window = [r for r in rows if start <= r['t'] <= end]
    # Reject skipped windows and long outages instead of fitting a partial tail.
    if (not window or window[0]['pm25'] < 0.9 * hi or window[-1]['pm25'] < 0.9 * lo
            or any(r['pm25'] <= background for r in window)
            or any(b['t'] - a['t'] > MAX_SAMPLE_GAP_SEC for a, b in zip(window, window[1:]))):
        return None
    pts = [(r['t'], math.log(r['pm25'] - background)) for r in window]
    if len(pts) < FIT_MIN_POINTS:
        return None
    mt = sum(t for t, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    sxx = sum((t - mt) ** 2 for t, _ in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    slope = sum((t - mt) * (y - my) for t, y in pts) / sxx if sxx else 0
    if slope >= 0 or not syy:
        return None
    ss_res = sum((y - my - slope * (t - mt)) ** 2 for t, y in pts)
    return dict(tau=-1 / slope, r2=1 - ss_res / syy, duration=end - start, points=len(pts))


def _window_fields(rows: list[dict], window: tuple[int, int], background: float = 0,
                   suffix: str = '') -> dict:
    hi, lo = window
    fit = fit_window(rows, hi, lo, background)
    return {
        f't_{hi}_to_{lo}_sec{suffix}': None if fit is None else round(fit['duration'], 1),
        f'tau_{hi}_{lo}_sec{suffix}': None if fit is None else round(fit['tau'], 1),
        f'r2_{hi}_{lo}{suffix}': None if fit is None else round(fit['r2'], 6),
        f'points_{hi}_{lo}{suffix}': None if fit is None else fit['points'],
    }


def _seconds(iso: str, t0: datetime) -> float:
    return (datetime.fromisoformat(iso) - t0).total_seconds()


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def analyze_run(run_dir: Path, background: float | None = None) -> dict:
    status = json.loads((run_dir / 'status.json').read_text(encoding='utf-8'))
    events = _read_jsonl(run_dir / 'events.jsonl')
    origin = next((e for e in events if e['event'] in T0_EVENTS), None)
    # Older logs may only record this timestamp in status.json. Never treat
    # removal as ventilation: the legacy protocol has a 30-second OFF interval.
    if origin is None and status.get('ventilation_started'):
        origin = dict(Time=status['ventilation_started'], event='status.ventilation_started')
    t0 = datetime.fromisoformat(origin['Time']) if origin else None
    samples = _read_jsonl(run_dir / 'pms_samples.jsonl')
    monotonic = (origin is not None and origin.get('elapsed_sec') is not None
                 and all(s.get('elapsed_sec') is not None for s in samples
                         if s.get('phase') != 'monitoring'))
    if monotonic:
        samples = [s for s in samples if s.get('elapsed_sec') is not None]
    rows = [dict(t=(s['elapsed_sec'] - origin['elapsed_sec'] if monotonic else
                    _seconds(s['Time'], t0) if t0 else -1),
                 pm1=s['pm1'], pm25=s['pm25'], pm10=s['pm10'],
                 quality=classify(s['pm1'], s['pm25'], s['pm10']))
            for s in samples]

    saturated = [i for i, r in enumerate(rows) if r['quality'] == 'suspected_saturation']
    after_exit = rows[saturated[-1] + 1:] if saturated else []
    exit_sec = after_exit[0]['t'] if after_exit else None
    recovery = [r for r in rows if r['t'] >= 0]
    settled = {thr: settle_time(recovery, thr) for thr in THRESHOLDS}
    primary = _window_fields(recovery, PRIMARY_WINDOW)
    fit = fit_window(recovery, *PRIMARY_WINDOW)
    fit_ok = fit is not None and fit['r2'] >= METRIC_MIN_R2  # gate BEFORE rounding
    over_limit = sum(r['pm25'] > EXPERIMENT_MAX_PM25 for r in rows)
    reasons = []
    if status.get('status') != 'complete':
        reasons.append('run_incomplete')
    if origin is None:
        reasons.append('missing_ventilation_start')
    if saturated:
        reasons.append('suspected_saturation')
    if over_limit:
        reasons.append('over_experiment_limit')
    if not fit_ok:
        reasons.append('primary_fit_failed')
    if any(b.get(k, 1) <= a.get(k, 0) for a, b in zip(samples, samples[1:])
           for k in ('seq', 'uptime_ms') if k in a and k in b):
        reasons.append('sample_counter_not_increasing')
    corrected = {}
    if background is not None:
        for window in (PRIMARY_WINDOW, SECONDARY_WINDOW):
            corrected.update(_window_fields(recovery, window, background, '_bg_corrected'))
    return {
        'run': run_dir.parent.name,
        'combination': run_dir.name,
        'intake': status.get('intake'),
        'exhaust': status.get('exhaust'),
        'pm_output': status.get('pm_output', 'atmospheric'),
        'time_origin': origin['event'] if origin else 'missing',
        'time_basis': 'elapsed_sec' if monotonic else 'wall_clock',
        'actual_smoke_sec': _round(status.get('actual_smoke_sec')),
        'suspected_saturation_samples': len(saturated),
        'over_experiment_limit_samples': over_limit,
        'above_1000_samples': sum(r['pm25'] > 1000 for r in rows),
        'suspected_saturation_exit_sec': _round(exit_sec) if origin else None,
        'peak_pm25_after_suspect_pattern': max((r['pm25'] for r in after_exit), default=None),
        'peak_observed_pm25': max((r['pm25'] for r in rows), default=None),
        'peak_within_limit_pm25': max((r['pm25'] for r in rows
                                      if r['quality'] == 'within_experiment_limit'), default=None),
        **primary,
        'fit_ok': fit_ok,
        'metric_ok': not reasons,
        'exclusion_reasons': ';'.join(reasons),
        **_window_fields(recovery, SECONDARY_WINDOW),
        'background_pm25': background,
        **corrected,
        **{f't_below_{thr}_sec': _round(settled[thr]) for thr in THRESHOLDS},
        'logged_recovery_sec': _round(status.get('recovery_elapsed_sec')),
    }


def analyze_all(root: Path, background: float | None = None) -> list[dict]:
    results = []
    for status_path in sorted(root.glob('*/*/status.json')):
        status = json.loads(status_path.read_text(encoding='utf-8'))
        if status.get('status') != 'complete':
            continue
        results.append(analyze_run(status_path.parent, background))
    return results


def write_summary(results: list[dict], path: Path) -> None:
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', nargs='?', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--background-pm25', type=float,
                        help='Independently justified constant asymptote; sensitivity analysis only')
    args = parser.parse_args(argv)
    if args.background_pm25 is not None and (not math.isfinite(args.background_pm25)
                                            or not 0 <= args.background_pm25 < 100):
        parser.error('--background-pm25 must be finite and 0 <= value < 100')
    results = analyze_all(args.root, args.background_pm25)
    if not results:
        print(f'No completed runs under {args.root}')
        return 1
    cols = ('combination', 'suspected_saturation_samples', 'tau_300_150_sec', 'r2_300_150', 'fit_ok', 'metric_ok',
            't_300_to_150_sec', 'tau_300_100_sec', 'r2_300_100', 't_300_to_100_sec',
            'logged_recovery_sec')
    print('\t'.join(cols))
    for r in results:
        print('\t'.join('-' if r[c] is None else str(r[c]) for c in cols))
    out = args.root / SUMMARY_NAME
    write_summary(results, out)
    print(f'Wrote {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
