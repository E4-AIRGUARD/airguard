import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fan_power import fan_power_w, combo_power_w, candidate_power_w, expected_rpm, recovery_energy_wh, observed_energy


class FanPowerTests(unittest.TestCase):
    def test_rated_and_stopped_power(self):
        self.assertAlmostEqual(fan_power_w(7500), 1.35)
        self.assertEqual(fan_power_w(0), 0)

    def test_missing_rpm_is_not_zero_power(self):
        for value in (None, float('nan'), -1, 'invalid'):
            self.assertIsNone(combo_power_w(value, 2730))

    def test_candidate_medians_and_asymmetry(self):
        self.assertEqual(expected_rpm(35, 'intake'), 2730)
        self.assertEqual(expected_rpm(100, 'exhaust'), 7110)
        self.assertAlmostEqual(candidate_power_w(35, 35), .1439634816)
        self.assertAlmostEqual(candidate_power_w(100, 100), 2.0827509696)
        self.assertNotEqual(candidate_power_w(35, 60), candidate_power_w(60, 35))

    def test_wh_conversion_and_unknown_recovery(self):
        self.assertAlmostEqual(recovery_energy_wh(2, 60), 1/30)
        self.assertIsNone(recovery_energy_wh(2, None))
        self.assertEqual(recovery_energy_wh(2, 0), 0)

    def test_integration_uses_elapsed_time_and_ignores_gaps(self):
        rows = [dict(timestamp=t, intake_rpm=7500, exhaust_rpm=7500) for t in (0, 1, 2, 10)]
        result = observed_energy(rows)
        self.assertAlmostEqual(result['energy_wh'], 2.7*2/3600)
        self.assertEqual(result['covered_seconds'], 2)
        self.assertEqual(result['excluded_seconds'], 8)
        self.assertIsNone(observed_energy(rows[:1])['energy_wh'])

    def test_runtime_and_db_use_explicit_units(self):
        from main_runner import relative_fan_energy
        self.assertAlmostEqual(relative_fan_energy(35, 35), candidate_power_w(35, 35))


if __name__ == '__main__':
    unittest.main()
