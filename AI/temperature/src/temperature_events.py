"""Derive temperature incidents from recorded telemetry without database writes."""
import math

from fan_power import observed_energy

LIMIT_C = 26.0
MAX_GAP_S = 2.5


def valid(row):
    try:
        return all(math.isfinite(float(row[key])) for key in ('timestamp', 'temperature_c'))
    except (KeyError, TypeError, ValueError):
        return False


def temperature_events(rows):
    """Rise >=0.08 C/s above 24.5 C or T>=26; recover after 3 stable samples.

    These are dashboard notification rules, not the controller's safety policy.
    A gap interrupts an event and never counts as recovery.
    """
    events = []
    active = None
    previous = None
    stable = 0
    samples = []

    def finish(status):
        active['status'] = status
        active['last_timestamp'] = samples[-1]['timestamp']
        active['duration_seconds'] = samples[-1]['timestamp'] - active['timestamp']
        active['recovered_at'] = samples[-1]['timestamp'] if status == 'recovered' else None
        active['energy'] = observed_energy(samples)
        active['history'] = list(samples)
        events.append(active.copy())

    for row in sorted(rows, key=lambda r: r['timestamp']):
        if not valid(row):
            if active:
                finish('interrupted')
            active = previous = None
            samples = []
            stable = 0
            continue
        dt = row['timestamp'] - previous['timestamp'] if previous else None
        if dt is not None and (dt <= 0 or dt > MAX_GAP_S):
            if active:
                finish('interrupted')
            active = previous = None
            stable = 0
            samples = []
        rate = (row['temperature_c'] - previous['temperature_c']) / dt if previous else None
        exceeded = row['temperature_c'] >= LIMIT_C
        rising = rate is not None and rate >= .08 and row['temperature_c'] >= 24.5
        if active is None and (exceeded or rising):
            active = dict(id=f"temperature-{row['timestamp']:.6f}", type='temperature',
                          timestamp=row['timestamp'], value=row['temperature_c'], limit=LIMIT_C,
                          trigger='temperature_limit' if exceeded else 'temperature_rise',
                          exceeded_limit=exceeded, peak_temperature_c=row['temperature_c'])
            samples = []
            stable = 0
        if active:
            samples.append(dict(row))
            active['exceeded_limit'] |= exceeded
            active['peak_temperature_c'] = max(active['peak_temperature_c'], row['temperature_c'])
            stable = stable + 1 if not exceeded and rate is not None and rate < .02 else 0
            if stable >= 3 and samples[-1]['timestamp'] - samples[-3]['timestamp'] >= 2:
                finish('recovered')
                active = None
                samples = []
                stable = 0
        previous = row
    if active:
        finish('active')
    return list(reversed(events))
