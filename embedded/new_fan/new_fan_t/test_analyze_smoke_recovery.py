import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyze_smoke_recovery as ana


def pms(t, pm1, pm25, pm10):
    return dict(t=t, pm1=pm1, pm25=pm25, pm10=pm10)


class ClassifyTests(unittest.TestCase):
    def test_identical_high_triplet_is_suspect(self):
        self.assertEqual(ana.classify(3327, 3327, 3327), 'suspected_saturation')

    def test_identical_low_triplet_is_not_saturated(self):
        self.assertEqual(ana.classify(5, 5, 5), 'within_experiment_limit')

    def test_experiment_limit_is_not_an_accuracy_claim(self):
        self.assertEqual(ana.classify(80, 1506, 2699), 'over_experiment_limit')
        self.assertEqual(ana.classify(25, 501, 1770), 'over_experiment_limit')

    def test_limit_boundary(self):
        self.assertEqual(ana.classify(25, 500, 600), 'within_experiment_limit')


class SettleTests(unittest.TestCase):
    def test_settle_time_ignores_early_dip_that_rebounds(self):
        rows = [pms(0, 3300, 3300, 3300), pms(1, 25, 400, 900),
                pms(2, 30, 700, 1500), pms(3, 30, 300, 600), pms(4, 10, 20, 30)]
        self.assertEqual(ana.settle_time(rows, 500), 3)

    def test_settle_time_none_when_never_below(self):
        self.assertIsNone(ana.settle_time([pms(0, 900, 900, 900)], 500))

    def test_settle_time_zero_when_already_below(self):
        self.assertEqual(ana.settle_time([pms(0, 1, 10, 12), pms(1, 1, 9, 10)], 20), 0)


class WindowFitTests(unittest.TestCase):
    def test_background_correction_recovers_true_tau_despite_high_raw_r2(self):
        rows = [pms(t, 1, 20 + 400 * math.exp(-t / 35), 1) for t in range(200)]
        raw = ana.fit_window(rows, 300, 150)
        corrected = ana.fit_window(rows, 300, 150, background=20)
        self.assertGreater(raw['r2'], 0.998)
        self.assertGreater(raw['tau'], 38)
        self.assertAlmostEqual(corrected['tau'], 35, places=8)

    def test_short_dense_window_cannot_pass_on_point_count_alone(self):
        rows = [pms(t / 100, 1, 400 * math.exp(-t / 100), 1) for t in range(200)]
        self.assertIsNone(ana.fit_window(rows, 300, 150))

    def test_skipped_upper_window_is_rejected(self):
        rows = [pms(0, 1, 400, 1)] + [pms(t, 1, 240-t, 1) for t in range(1, 120)]
        self.assertIsNone(ana.fit_window(rows, 300, 150))

    def test_large_gap_or_time_reversal_is_rejected(self):
        rows = [pms(t, 1, 400 * math.exp(-t/35), 1) for t in range(100)]
        self.assertIsNone(ana.fit_window([r for r in rows if not 15 <= r['t'] <= 25], 300, 150))
        self.assertIsNone(ana.fit_window(rows[:30] + [rows[20]] + rows[30:], 300, 150))

    def test_invalid_background_is_rejected(self):
        for value in (-1, float('nan'), 150):
            with self.assertRaises(ValueError):
                ana.fit_window([], 300, 150, value)

    def test_fit_recovers_known_time_constant(self):
        rows = [pms(t, 1, round(600 * math.exp(-t / 35)), 1) for t in range(0, 200)]
        fit = ana.fit_window(rows, 300, 150)
        self.assertAlmostEqual(fit['tau'], 35, delta=1.5)
        self.assertGreater(fit['r2'], 0.99)
        self.assertAlmostEqual(fit['duration'], 35 * math.log(2), delta=2)

    def test_window_never_entered_from_above_is_none(self):
        rows = [pms(t, 1, 250 - t, 1) for t in range(0, 100)]
        self.assertIsNone(ana.fit_window(rows, 300, 150))

    def test_window_never_reaching_low_end_is_none(self):
        rows = [pms(t, 1, 400 - t, 1) for t in range(0, 200)]
        self.assertIsNone(ana.fit_window(rows, 300, 150))

    def test_too_few_points_is_none(self):
        rows = [pms(0, 1, 400, 1), pms(1, 1, 290, 1), pms(2, 1, 200, 1), pms(3, 1, 140, 1)]
        self.assertIsNone(ana.fit_window(rows, 300, 150))


class RunTests(unittest.TestCase):
    def write_run(self, root, samples, status='complete'):
        d = Path(root) / '20261007_000000_000000' / '02_intake_weak_exhaust_medium'
        d.mkdir(parents=True)
        (d / 'status.json').write_text(json.dumps(dict(status=status, intake=35, exhaust=60,
                                                        recovery_elapsed_sec=9.0)))
        (d / 'events.jsonl').write_text(json.dumps(
            {'Time': '2026-10-07T17:22:00.000+09:00', 'event': 'direct_ventilation_started'}) + '\n')
        (d / 'pms_samples.jsonl').write_text(''.join(
            json.dumps({'Time': f'2026-10-07T17:{m:02d}:{s:02d}.000+09:00',
                        'pm1': a, 'pm25': b, 'pm10': c}) + '\n'
            for m, s, a, b, c in samples))
        return d

    def test_analyze_run_reports_saturation_and_settle_times(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.write_run(root, [
                (21, 50, 3327, 3327, 3327),
                (22, 0, 3322, 3322, 3322),
                (22, 2, 25, 651, 1770),
                (22, 4, 20, 400, 600),
                (22, 6, 10, 90, 120),
                (22, 8, 5, 18, 22),
            ])
            r = ana.analyze_run(d)
        self.assertEqual(r['combination'], '02_intake_weak_exhaust_medium')
        self.assertEqual((r['intake'], r['exhaust']), (35, 60))
        self.assertEqual(r['suspected_saturation_samples'], 2)
        self.assertEqual(r['suspected_saturation_exit_sec'], 2)
        self.assertEqual(r['peak_pm25_after_suspect_pattern'], 651)
        self.assertEqual(r['t_below_300_sec'], 6)
        self.assertEqual(r['t_below_20_sec'], 8)
        self.assertIsNone(r['tau_300_150_sec'])   # 2 points: not enough to fit
        self.assertFalse(r['metric_ok'])

    def test_analyze_run_primary_metric_on_clean_decay(self):
        with tempfile.TemporaryDirectory() as root:
            samples = []
            for k in range(0, 150):
                pm = round(20 + 400 * math.exp(-k / 35))
                samples.append((22 + k // 60, k % 60, max(1, pm // 4), pm, pm * 2))
            d = self.write_run(root, samples)
            r = ana.analyze_run(d)
        self.assertEqual(r['suspected_saturation_samples'], 0)
        self.assertTrue(r['metric_ok'])
        self.assertGreater(r['r2_300_150'], 0.99)
        self.assertAlmostEqual(r['tau_300_150_sec'], 37, delta=5)  # floor 20 makes raw tau slightly longer
        self.assertIsNotNone(r['t_300_to_100_sec'])

    def decay_samples(self):
        return [(22 + k // 60, k % 60, 1, round(400*math.exp(-k/35)), 500)
                for k in range(100)]

    def test_high_r2_does_not_override_high_exposure(self):
        for triplet in ((3327,3327,3327), (80,800,1000)):
            with self.subTest(triplet=triplet), tempfile.TemporaryDirectory() as root:
                d = self.write_run(root, [(21,59,*triplet)] + self.decay_samples())
                r = ana.analyze_run(d)
                self.assertTrue(r['fit_ok'])
                self.assertFalse(r['metric_ok'])
                self.assertIn('over_experiment_limit', r['exclusion_reasons'])

    def test_rounded_r2_does_not_authorize_metric(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.write_run(root, self.decay_samples())
            with patch.object(ana, 'fit_window', return_value=dict(tau=35, r2=0.9899999, duration=24, points=24)):
                r = ana.analyze_run(d)
            self.assertEqual(r['r2_300_150'], 0.99)
            self.assertFalse(r['fit_ok'])

    def test_missing_ventilation_event_does_not_fall_back_to_removal(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.write_run(root, self.decay_samples())
            event_path = d / 'events.jsonl'
            event_path.write_text(event_path.read_text().replace('direct_ventilation_started', 'removal_confirmed_by_user'))
            r = ana.analyze_run(d)
            self.assertFalse(r['metric_ok'])
            self.assertIn('missing_ventilation_start', r['exclusion_reasons'])

    def test_legacy_off_interval_uses_ventilation_origin(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.write_run(root, self.decay_samples())
            (d / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
                dict(Time='2026-10-07T17:21:30+09:00', event='removal_confirmed_by_user'),
                dict(Time='2026-10-07T17:22:00+09:00', event='ventilation_started')]))
            r = ana.analyze_run(d)
            self.assertEqual(r['t_below_300_sec'], 11)

    def test_elapsed_time_is_used_despite_wall_clock_jump(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.write_run(root, self.decay_samples())
            events = dict(Time='2026-10-07T17:22:00+09:00', event='direct_ventilation_started', elapsed_sec=100)
            (d / 'events.jsonl').write_text(json.dumps(events))
            samples = ana._read_jsonl(d / 'pms_samples.jsonl')
            for i, sample in enumerate(samples):
                sample.update(elapsed_sec=100+i, seq=i+1, uptime_ms=i*1000)
                sample['Time'] = events['Time']
            (d / 'pms_samples.jsonl').write_text('\n'.join(map(json.dumps, samples)))
            r = ana.analyze_run(d)
            self.assertTrue(r['metric_ok'])
            self.assertEqual(r['time_basis'], 'elapsed_sec')
            samples[20]['seq'] = samples[19]['seq']
            (d / 'pms_samples.jsonl').write_text('\n'.join(map(json.dumps, samples)))
            r = ana.analyze_run(d)
            self.assertFalse(r['metric_ok'])
            self.assertIn('sample_counter_not_increasing', r['exclusion_reasons'])

    def test_failed_runs_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_run(root, [(22, 0, 10, 10, 10)], status='failed')
            self.assertEqual(ana.analyze_all(Path(root)), [])


if __name__ == '__main__':
    unittest.main()
