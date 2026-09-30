"""Batch ordering, duration and interruption tests; no real hardware."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import measure_clean_air_matrix as matrix


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def run_matrix(self, results=None, extra=()):
        with patch.object(matrix, 'measure', side_effect=results, return_value=True) as measure, \
                contextlib.redirect_stdout(io.StringIO()):
            matrix.main(['--out-root', str(self.folder), *extra])
        state = json.loads(next(self.folder.glob('*/batch.json')).read_text(encoding='utf-8'))
        return state, measure

    def test_default_nine_pairs_are_each_five_minutes(self):
        state, measure = self.run_matrix()
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(len(state['completed']), 9)
        self.assertEqual(state['duration_sec'], 300)
        self.assertEqual(state['image_interval_sec'], 1)
        actual = []
        for call in measure.call_args_list:
            args = call.args[0]
            actual.append((int(args[args.index('--intake') + 1]), int(args[args.index('--exhaust') + 1])))
            self.assertEqual(args[args.index('--duration-min') + 1], '5.0')
            self.assertEqual(args[args.index('--experiment') + 1], 'clean-air')
            self.assertEqual(args[args.index('--image-interval-sec') + 1], '1')
        self.assertEqual(actual, [(i, o) for i in (35, 60, 100) for o in (35, 60, 100)])

    def test_q_stops_remaining_combinations(self):
        state, measure = self.run_matrix([True, False])
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(len(state['completed']), 1)
        self.assertEqual(measure.call_count, 2)

    def test_failure_stops_remaining_combinations_and_is_recorded(self):
        with patch.object(matrix, 'measure', side_effect=[True, SystemExit('camera lost')]) as measure, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            matrix.main(['--out-root', str(self.folder)])
        state = json.loads(next(self.folder.glob('*/batch.json')).read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], 'camera lost')
        self.assertEqual(len(state['completed']), 1)
        self.assertEqual(measure.call_count, 2)

    def test_dry_run_does_not_measure_or_create_run(self):
        with patch.object(matrix, 'measure') as measure, contextlib.redirect_stdout(io.StringIO()) as output:
            matrix.main(['--out-root', str(self.folder), '--dry-run'])
        measure.assert_not_called()
        self.assertIn('300초', output.getvalue())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_bad_duration_rejected_before_measurement(self):
        with patch.object(matrix, 'measure') as measure, contextlib.redirect_stderr(io.StringIO()):
            for duration in ('0', '-1', 'nan', 'inf'):
                with self.assertRaises(SystemExit):
                    matrix.main(['--duration-sec', duration, '--out-root', str(self.folder)])
        measure.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
