"""Derive temperature-rise/over-limit events from stored raw sensor readings."""
from bisect import bisect_left, bisect_right
from collections import deque

from fan_power import observed_energy


def rate(history, lag):
    if len(history) < 2:
        return 0.
    now, temperature = history[-1]
    stamp, previous = min(list(history)[:-1], key=lambda r: abs(now-r[0]-lag))
    dt = now-stamp
    return (temperature-previous)/dt if dt > 0 and abs(dt-lag) <= .6 else 0.


def temperature_events(rows):
    events, current = [], None
    history = deque(maxlen=8)
    previous, rising_count, low_count, low_start = None, 0, 0, None
    for row in rows:
        stamp, temperature = row.get('timestamp'), row.get('temperature_c')
        if stamp is None or temperature is None:
            continue
        gap = previous is not None and stamp-previous > 2.5
        if gap:
            if current is not None:
                current['status'] = 'interrupted'
                events.append(current)
            current, rising_count, low_count = None, 0, 0
            history.clear()
        history.append((stamp, temperature))
        r1, r3 = rate(history, 1), rate(history, 3)
        rising = len(history) >= 4 and temperature >= 24.5 and r1 >= .08 and r3 >= .04 and temperature+5*r3 >= 25.5
        rising_count = rising_count+1 if rising else 0
        over = temperature >= 26
        risk = over or rising_count >= 2
        if risk and current is None:
            current = dict(id=f'temperature-{stamp:.6f}', type='temperature', start=stamp,
                end=None, duration_seconds=None, peak_c=temperature, threshold_c=26.,
                start_temperature_c=temperature, status='active', left_censored=previous is None or gap,
                trigger='temperature_over_limit' if over else 'temperature_rise', exceeded_limit=over,
                source='stored_sensor_rules')
        if current is not None:
            current.update(peak_c=max(current['peak_c'],temperature), last_temperature_c=temperature,
                last_observed_at=stamp, rate_1s_c_per_s=r1, rate_3s_c_per_s=r3,
                exceeded_limit=current['exceeded_limit'] or over)
            if temperature < 26 and r1 < .02 and r3 < .02:
                if low_count == 0:
                    low_start = stamp
                low_count += 1
            else:
                low_count, low_start = 0, None
            if low_count >= 3 and stamp-low_start >= 2:
                current.update(status='recovered',end=stamp,duration_seconds=stamp-current['start'])
                events.append(current)
                current, low_count, low_start = None, 0, None
        previous = stamp
    if current is not None:
        events.append(current)
    times = [r['timestamp'] for r in rows]
    for event in events:
        first = bisect_left(times,event['start'])
        last = bisect_right(times,event['last_observed_at'])
        event.update(observed_energy(rows[first:last]))
    return events
