"""RPM-based fan power approximation; not an electrical measurement."""
import math
from bisect import bisect_right

VOLTAGE_V = 5.0
RATED_CURRENT_A = 0.27
MAX_RPM = 7500.0
PWM_POINTS = (0.0, 35.0, 60.0, 100.0)
RPM_POINTS = {
    "intake": (0.0, 2730.0, 4620.0, 6630.0),
    "exhaust": (0.0, 2910.0, 5010.0, 7110.0),
}
POWER_METHOD = "rated_power_times_rpm_ratio_cubed_v1"


def fan_power_w(rpm):
    """Missing/invalid RPM stays unknown, while a reported zero means stopped."""
    if rpm is None:
        return None
    try:
        rpm = float(rpm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rpm) or rpm < 0:
        return None
    return VOLTAGE_V * RATED_CURRENT_A * (rpm / MAX_RPM) ** 3


def combo_power_w(intake_rpm, exhaust_rpm):
    powers = (fan_power_w(intake_rpm), fan_power_w(exhaust_rpm))
    return None if None in powers else sum(powers)


def expected_rpm(pwm, fan):
    """Use measured stage medians; other PWM values use linear interpolation."""
    pwm = float(pwm)
    if not math.isfinite(pwm) or not 0 <= pwm <= 100:
        raise ValueError("PWM must be finite and between 0 and 100")
    values = RPM_POINTS[fan]
    if pwm == 100:
        return values[-1]
    index = bisect_right(PWM_POINTS, pwm) - 1
    fraction = (pwm - PWM_POINTS[index]) / (PWM_POINTS[index + 1] - PWM_POINTS[index])
    return values[index] + fraction * (values[index + 1] - values[index])


def candidate_power_w(intake_pwm, exhaust_pwm):
    return combo_power_w(expected_rpm(intake_pwm, "intake"), expected_rpm(exhaust_pwm, "exhaust"))


def recovery_energy_wh(power_w, seconds):
    if power_w is None or seconds is None:
        return None
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in (power_w, seconds)):
        return None
    return power_w * seconds / 3600.0


def observed_energy(rows, max_gap_s=2.5):
    """Left-hold integration over valid RPM intervals, never across missing data."""
    energy_j = covered = excluded = 0.0
    for current, following in zip(rows, rows[1:]):
        interval = float(following["timestamp"]) - float(current["timestamp"])
        if not math.isfinite(interval) or interval <= 0:
            continue
        power = combo_power_w(current.get("intake_rpm"), current.get("exhaust_rpm"))
        if interval > max_gap_s or power is None:
            excluded += interval
            continue
        energy_j += power * interval
        covered += interval
    return dict(energy_wh=energy_j / 3600 if covered else None,
                covered_seconds=covered, excluded_seconds=excluded)
