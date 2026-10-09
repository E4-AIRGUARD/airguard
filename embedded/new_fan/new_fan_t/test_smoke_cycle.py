import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import measure_smoke_cycle as smoke


def sample(seq, pm=10):
    return dict(seq=seq, uptime_ms=seq*1000, pm1=pm, pm25=pm, pm10=pm)


class CycleTests(unittest.TestCase):
    def ready_cycle(self):
        c = smoke.SmokeCycle(0)
        for n in range(29, 32):
            c.accept(sample(n), n)
        return c

    def test_parser_rejects_bad_packets(self):
        for line in ('DATA,1,2', 'PMS_SAMPLE,0,0,0,0,0',
                     'PMS_SAMPLE,1,100,1,-1,1', 'PMS_SAMPLE,1,100,1,nan,1',
                     'PMS_SAMPLE,1,100,1,65536,1'):
            self.assertIsNone(smoke.parse_pms(line))
        self.assertEqual(smoke.parse_pms('PMS_SAMPLE,1,100,10,20,30')['pm25'], 20)

    def test_duplicate_cached_frames_cannot_authorize_start(self):
        c = smoke.SmokeCycle(0)
        for n in range(30, 35):
            c.accept(sample(1), n)
        self.assertEqual(c.low_count, 1)
        self.assertFalse(c.ready(34))

    def test_warmup_and_expired_sample(self):
        c = self.ready_cycle()
        self.assertTrue(c.ready(31))
        self.assertFalse(c.ready(38))
        c.accept(sample(40), 40)
        self.assertEqual(c.low_count, 1)
        with self.assertRaises(ValueError):
            c.start(40)

    def test_full_protocol_and_actual_removal(self):
        c = self.ready_cycle()
        c.start(31)
        c.accept(sample(40), 40)
        self.assertEqual(c.tick(40.99), [])
        self.assertEqual(c.tick(41), ['insert_requested'])
        c.command('inserted', 42)
        # High PM no longer ends smoke generation; 60 seconds is the criterion.
        # Saturation/over-limit are flagged once, without changing the phase.
        self.assertEqual(c.accept(sample(43, 3354), 43), ['pm_over_limit', 'pm_suspected_saturation'])
        self.assertEqual(c.accept(sample(44, 3354), 44), [])
        self.assertEqual(c.phase, 'smoking')
        self.assertEqual(c.tick(101.99), [])
        self.assertEqual(c.tick(102), ['remove_requested'])
        for n in range(103, 106):
            self.assertEqual(c.accept(sample(n, 5), n), [])
        self.assertEqual(c.phase, 'await_remove')  # No inferred physical removal.
        c.command('removed', 106)
        self.assertEqual(c.tick(150), [])  # Actual fan stop must first be verified.
        self.assertEqual(c.confirm_fans_off(151), 'fans_off_confirmed')
        for n in (160,170,180):
            self.assertEqual(c.accept(sample(n, 10), n), [])  # No early finish while OFF.
        self.assertEqual(c.tick(180.99), [])
        self.assertEqual(c.tick(181), ['ventilation_started'])
        c.ventilation_verified = True
        for n in range(182, 191):
            self.assertEqual(c.accept(sample(n, 20), n), [])
        self.assertEqual(c.accept(sample(191, 20), 191), ['recovery_complete'])
        self.assertEqual(c.peak, 3354)
        self.assertEqual(c.peak_within_limit, 20)  # exclude suspect high frames from this field

    def test_smoke_duration_is_configurable(self):
        c = smoke.SmokeCycle(0, smoke_sec=8)
        for n in range(29, 32):
            c.accept(sample(n), n)
        c.start(31)
        c.accept(sample(40), 40)
        c.tick(41)
        c.command('inserted', 42)
        self.assertEqual(c.tick(49.99), [])
        self.assertEqual(c.tick(50), ['remove_requested'])

    def test_over_limit_without_saturation(self):
        c = self.ready_cycle()
        c.start(31)
        over = dict(seq=40, uptime_ms=40000, pm1=80, pm25=1200, pm10=2400)
        self.assertIn('pm_over_limit', c.accept(over, 40))
        self.assertFalse(c.suspected_saturation)
        self.assertEqual(c.peak_within_limit, 10)
        self.assertEqual(c.peak, 1200)

    def test_peak_verdict(self):
        v = smoke.peak_verdict
        self.assertEqual(v(3300, True, True), 'excluded_suspected_saturation')
        self.assertEqual(v(501, False, True), 'excluded_over_experiment_limit')
        self.assertEqual(v(501, False, False), 'excluded_over_experiment_limit')
        self.assertEqual(v(None, False, False), 'no_samples')
        self.assertEqual(v(300, False, False), 'too_low')
        self.assertEqual(v(301, False, False), 'below_target')
        self.assertEqual(v(349, False, False), 'below_target')
        self.assertEqual(v(350, False, False), 'target')
        self.assertEqual(v(450, False, False), 'target')
        self.assertEqual(v(451, False, False), 'above_target')
        self.assertEqual(v(500, False, False), 'above_target')

    def test_parser_rejects_bad_smoke_duration(self):
        for bad in ('0', '-1', 'nan', '121'):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                smoke.main(['--smoke-sec', bad])

    def test_recovery_broken_by_high_value(self):
        c = self.ready_cycle()
        c.phase = 'await_remove'
        c.command('removed', 32)
        c.confirm_fans_off(33)
        c.accept(sample(63), 63)
        c.tick(63)
        c.ventilation_verified = True
        for n, pm in ((70, 20), (71, 21), (72, 19), (73, 19)):
            self.assertEqual(c.accept(sample(n, pm), n), [])
        self.assertEqual(c.accept(sample(74, 19), 74), ['recovery_complete'])

    def test_early_removal_starts_direct_recovery_and_cancels_timer(self):
        c = smoke.SmokeCycle(0, smoke_fans_off=True, smoke_sec=10)
        c.phase, c.inserted_at = 'smoking', 50
        self.assertEqual(c.command('removed', 52), 'removal_confirmed_by_user')
        self.assertEqual(c.ventilation_at, 52)
        self.assertEqual(c.removed_at - c.inserted_at, 2)
        self.assertEqual(c.tick(60), [])

    def test_manual_dose_accepts_removal_without_fabricating_insertion(self):
        c = smoke.SmokeCycle(0, smoke_fans_off=True, smoke_sec=2, manual_smoke=True)
        c.phase = 'await_insert'
        self.assertEqual(c.command('removed', 52), 'removal_confirmed_by_user')
        self.assertEqual(c.phase, 'recovery')
        self.assertIsNone(c.inserted_at)
        self.assertEqual(c.ventilation_at, 52)

    def test_low_pm_cannot_finish_before_fan_verification(self):
        c = smoke.SmokeCycle(0, smoke_fans_off=True)
        c.phase, c.inserted_at = 'smoking', 0
        c.command('removed', 2)
        for n in range(3, 20):
            self.assertEqual(c.accept(sample(n), n), [])
        c.ventilation_verified = True
        self.assertEqual(c.accept(sample(20), 20), ['recovery_complete'])

    def test_initial_low_readings_do_not_finish_before_delayed_smoke_response(self):
        c = smoke.SmokeCycle(0, smoke_fans_off=True)
        c.phase, c.inserted_at = 'smoking', 0
        c.command('removed', 2)
        c.ventilation_verified = True
        for n in range(3, 8):
            self.assertEqual(c.accept(sample(n), n), [])
        c.accept(sample(8, 400), 8)
        self.assertEqual(c.phase, 'recovery')
        for n in (10, 11):
            self.assertEqual(c.accept(sample(n), n), [])
        self.assertEqual(c.accept(sample(12), 12), ['recovery_complete'])

    def test_restart_fails_but_post_start_pm_variation_is_recorded(self):
        c = self.ready_cycle()
        with self.assertRaises(OSError):
            c.accept(sample(1), 32)
        c.start(31)
        self.assertEqual(c.accept(sample(32,21),32),['baseline_pm_above_start_limit'])
        self.assertEqual(c.phase,'baseline')

    def test_stop_ignores_interleaved_non_data(self):
        stopped = b'DATA,100,0,0,0,0,25,40,1010,10,10,10,0,0\n'
        class Port:
            lines = iter([stopped, b'PMS_SAMPLE,1,100,10,10,10\n', b'[FAN]\n', stopped])
            in_waiting = 1
            def reset_input_buffer(self): pass
            def write(self, data): self.written = data
            def read(self, size): return next(self.lines)
        port = Port()
        self.assertTrue(smoke.stop_fans(port, clock=lambda: 0))
        self.assertEqual(port.written, b'0\n')

    def test_split_serial_line_is_preserved(self):
        class Port:
            in_waiting = 1
            chunks = iter([b'PMS_SAMPLE,1,100,', b'', b'10,20,30\n'])
            def read(self, size): return next(self.chunks)
        reader = smoke.SerialLines(Port())
        self.assertEqual(reader.read(), '')
        self.assertEqual(reader.read(), '')
        self.assertEqual(smoke.parse_pms(reader.read())['pm25'], 20)


class RunnerTests(unittest.TestCase):
    def run_fake(self, root, mode, stale=False, fan_failure=False, off_failure=False, smoke_fans_off=False,
                 extra_args=(), recovery_fan_failure=False, pm_profile=None, action_override=None,
                 missing_data=False):
        clock = [0.0]
        commands = [None]
        dose = float(extra_args[extra_args.index('--smoke-sec')+1]) if '--smoke-sec' in extra_args else 60
        removal_time = 56+dose+2 if smoke_fans_off else 53+dose+2
        class Port:
            is_open = False
            duty = 0
            duty_out = 0
            writes = []
            sequence = 0
            in_waiting = 1
            writes_at = []
            def open(self): self.is_open = True
            def close(self): self.is_open = False
            def reset_input_buffer(self): pass
            def write(self, data):
                self.writes.append(data)
                self.writes_at.append(clock[0])
                self.duty = int(data.split()[1]) if data.startswith(b'F ') else 0
                self.duty_out = int(data.split()[2]) if data.startswith(b'F ') else 0
            def read(self, size):
                clock[0] += 0.5
                t = clock[0]
                actions = {40: 'start', 53: 'inserted', removal_time: 'removed'}
                if smoke_fans_off: actions = {40:'start',56:'inserted',removal_time:'removed'}
                if action_override is not None: actions = action_override
                if mode == 'monitor': actions = {5: 'quit'}
                if t in actions: commands[0].put(actions[t])
                if t >= 200: commands[0].put('quit')
                if t % 1 == 0:
                    self.sequence += 1
                    if stale and t >= 45: return b'[FAN]\n'
                    pm = pm_profile(t) if pm_profile else (220 if 54 <= t < 160 else 10)
                    return f'PMS_SAMPLE,{self.sequence},{int(t*1000)},{pm},{pm},{pm}\n'.encode()
                duty = 0 if fan_failure else self.duty
                duty_out = 0 if fan_failure else self.duty_out
                if recovery_fan_failure and t >= removal_time: duty = duty_out = 0
                if off_failure and 116 <= t < 150: duty = 35
                if missing_data: return b'[FAN]\n'
                return f'DATA,{int(t*1000)},{duty},{duty*100},{duty_out},{duty_out*100},25,40,1000,10,10,10,0,0\n'.encode()
        port = Port()
        class Camera:
            count = 0
            recorded_times = []
            def __init__(self, *a, **kw): self.active = False
            def open(self): pass
            def begin(self): self.active = True
            def pause(self): self.active = False
            def close(self): pass
            def check(self):
                if self.active:
                    self.count += 1
                    self.recorded_times.append(clock[0])
        def reader(q): commands[0] = q
        argv = ['--mode', mode, '--out-root', str(root)]
        if smoke_fans_off: argv.append('--smoke-fans-off')
        argv.extend(extra_args)
        with patch('serial.Serial', return_value=port), patch('thermal_camera.CameraRecorder', Camera), \
             patch.object(smoke, 'read_commands', reader), \
             patch.object(smoke.time, 'monotonic', side_effect=lambda: clock[0]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = smoke.main(argv)
        port.output = output.getvalue()
        port.camera_times = Camera.recorded_times
        return result, port

    def test_monitor_does_not_write_or_record(self):
        with tempfile.TemporaryDirectory() as temp:
            result, port = self.run_fake(Path(temp), 'monitor')
            self.assertEqual(result, 0)
            self.assertEqual(port.writes, [])
            self.assertEqual(list(Path(temp).iterdir()), [])
            self.assertFalse(port.is_open)

    def test_second_experiment_stops_before_smoke_and_ventilates_on_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            result,port=self.run_fake(root,'experiment',smoke_fans_off=True)
            self.assertEqual(result,0)
            self.assertEqual(port.writes,[b'F 35 35\n',b'F 0 0\n',b'F 35 35\n',b'0\n'])
            self.assertLess(port.writes_at[1],56)
            self.assertLessEqual(abs(port.writes_at[2]-118),1)
            events=[json.loads(x) for x in next(root.rglob('events.jsonl')).read_text().splitlines()]
            names=[e['event'] for e in events]
            self.assertLess(names.index('smoke_fan_stop_requested'),names.index('insert_requested'))
            self.assertIn('direct_ventilation_started',names)
            self.assertNotIn('fans_off_confirmed',names)
            state=json.loads(next(root.rglob('status.json')).read_text())
            self.assertEqual(state['status'],'complete')
            self.assertEqual(state['fans_off_sec'],0)
            self.assertGreaterEqual(state['actual_fans_off_sec'],60)
            self.assertTrue(any(53<t<115 for t in port.camera_times))

    def test_pilot_run_records_dose_and_peak_verdict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                                         extra_args=['--smoke-sec', '8'])
            self.assertEqual(result, 0)
            state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(state['smoke_sec'], 8)
            self.assertIn('smoke8', state['protocol'])
            self.assertEqual(state['peak_within_limit_pm25'], 220)
            self.assertEqual(state['peak_verdict'], 'too_low')
            self.assertFalse(state['suspected_saturation'])
            self.assertFalse(state['over_limit'])
            self.assertAlmostEqual(state['actual_smoke_sec'], 10)
            self.assertAlmostEqual(state['removal_delay_after_nominal_sec'], 2)

    def test_all_short_pilot_durations_and_strong_fans(self):
        for duration, combination in ((2, 1), (4, 9), (6, 2), (8, 1), (10, 1)):
            with self.subTest(duration=duration, combination=combination), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                    extra_args=['--smoke-sec', str(duration), '--combination', str(combination)],
                    pm_profile=lambda t: 400 if 56 <= t < 80 else 10)
                self.assertEqual(result, 0, port.output)
                state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
                events = [json.loads(x) for x in next(root.rglob('events.jsonl')).read_text(encoding='utf-8').splitlines()]
                times = {e['event']: e.get('elapsed_sec') for e in events}
                self.assertAlmostEqual(times['remove_requested'] - times['insertion_confirmed_by_user'], duration)
                self.assertAlmostEqual(state['actual_smoke_sec'], duration+2)
                self.assertAlmostEqual(state['removal_prompt_lateness_sec'], 0)
                self.assertAlmostEqual(state['removal_delay_after_nominal_sec'], 2)
                self.assertFalse(state['removed_before_nominal'])
                self.assertEqual(state['peak_verdict'], 'target')
                pair = smoke.COMBINATIONS[combination-1][1:]
                fan_cmd = f'F {pair[0]} {pair[1]}\n'.encode()
                self.assertEqual(port.writes, [fan_cmd, b'F 0 0\n', fan_cmd, b'0\n'])
                self.assertLess(times['ventilation_verified'], times['recovery_complete'])
                self.assertIn(f'연기 생성 {duration}초 종료', port.output)

    def test_early_removal_is_logged_even_before_nominal_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                extra_args=['--smoke-sec', '10'], action_override={40:'start',56:'inserted',58:'removed'})
            self.assertEqual(result, 0, port.output)
            state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(state['actual_smoke_sec'], 2)
            self.assertTrue(state['removed_before_nominal'])
            self.assertLessEqual(port.writes_at[2], 58.5)
            self.assertNotIn('연기 생성 10초 종료', port.output)

    def test_manual_chat_dose_runs_recovery_with_unknown_insertion_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                extra_args=['--smoke-sec', '2', '--manual-smoke'], action_override={40:'start',58:'removed'})
            self.assertEqual(result, 0, port.output)
            state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(state['status'], 'complete')
            self.assertIsNone(state['actual_smoke_sec'])
            self.assertEqual(state['dose_timing_mode'], 'operator_manual')
            self.assertLessEqual(port.writes_at[2], 58.5)
            names = [json.loads(x)['event'] for x in next(root.rglob('events.jsonl')).read_text(encoding='utf-8').splitlines()]
            self.assertIn('insertion_time_unknown_operator_timed', names)
            self.assertNotIn('insertion_confirmed_by_user', names)

    def test_low_dose_cannot_mask_recovery_fan_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                extra_args=['--smoke-sec', '2'], recovery_fan_failure=True, pm_profile=lambda t: 10)
            self.assertEqual(result, 1)
            state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(state['status'], 'failed')
            self.assertIn('Fan duty/RPM', state['error'])
            self.assertEqual(port.writes[-1], b'0\n')

    def test_no_environment_keeps_asymmetric_fan_columns_aligned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', smoke_fans_off=True,
                extra_args=['--smoke-sec', '2', '--manual-smoke', '--combination', '2',
                            '--no-environment'], action_override={40:'start',58:'removed'})
            self.assertEqual(result, 0, port.output)
            with next(root.rglob('sensors.csv')).open(encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, ['Time', 'Exhaust_PWM', 'Intake_PWM',
                                                    'Heat_Load', 'Exhaust_RPM', 'Intake_RPM', 'PM2.5'])
                rows = list(reader)
            self.assertTrue(rows)
            self.assertTrue(all(None not in row and None not in row.values() for row in rows))
            active = next(row for row in rows if row['Intake_PWM'] == '35')
            self.assertEqual(active['Exhaust_PWM'], '60')
            self.assertEqual(active['Intake_RPM'], '3500')
            self.assertEqual(active['Exhaust_RPM'], '6000')
            self.assertEqual(active['Heat_Load'], '0')
            self.assertEqual(active['PM2.5'], '10')
            state = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertTrue(state['environment_columns_omitted'])
            self.assertNotIn('temperature', state['pre_start_sensor'])
            self.assertNotIn('Temp=', port.output)

    def test_missing_fan_telemetry_cannot_authorize_start(self):
        with tempfile.TemporaryDirectory() as temp:
            result, port = self.run_fake(Path(temp), 'experiment', missing_data=True)
            self.assertEqual(result, 1)
            self.assertEqual(port.writes, [])

    def test_end_to_end_records_and_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment')
            self.assertEqual(result, 0)
            self.assertEqual(port.writes, [b'F 35 35\n', b'F 0 0\n', b'F 35 35\n', b'0\n'])
            self.assertGreaterEqual(port.writes_at[2]-port.writes_at[1], 30)
            self.assertTrue(any(port.writes_at[1]<t<port.writes_at[2] for t in port.camera_times))
            status = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(status['status'], 'complete')
            self.assertTrue(status['stop_verified'])
            self.assertEqual(status['peak_pm25'], 220)
            self.assertGreater(status['rows'], 0)
            self.assertGreaterEqual(status['actual_fans_off_sec'], 30)
            self.assertGreaterEqual(status['actual_smoke_sec'], 60)
            self.assertLess(status['recovery_elapsed_sec'], status['removal_to_recovery_sec'])
            events = [json.loads(x)['event'] for x in next(root.rglob('events.jsonl')).read_text(encoding='utf-8').splitlines()]
            self.assertIn('insertion_confirmed_by_user', events)
            self.assertIn('removal_confirmed_by_user', events)
            self.assertIn('recovery_complete', events)
            self.assertIn('fans_off_confirmed', events)
            self.assertIn('ventilation_started', events)
            header = next(root.rglob('sensors.csv')).read_text(encoding='utf-8').splitlines()[0]
            self.assertEqual(header, ','.join(smoke.CSV_HEADER))
            self.assertTrue(any(row.split(',')[3:5]==['0','0'] for row in
                                next(root.rglob('sensors.csv')).read_text().splitlines()[1:]))

    def test_off_failure_stops_run_instead_of_starting_ventilation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', off_failure=True)
            self.assertEqual(result, 1)
            self.assertEqual(port.writes.count(b'F 35 35\n'), 1)
            status = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(status['status'], 'failed')

    def test_sensor_failure_stops_fans_and_keeps_partial_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', stale=True)
            self.assertEqual(result, 1)
            self.assertEqual(port.writes[-1], b'0\n')
            status = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertEqual(status['status'], 'failed')
            self.assertTrue(status['stop_verified'])
            self.assertTrue(next(root.rglob('sensors.csv')).exists())

    def test_fan_failure_cannot_be_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result, port = self.run_fake(root, 'experiment', fan_failure=True)
            self.assertEqual(result, 1)
            status = json.loads(next(root.rglob('status.json')).read_text(encoding='utf-8'))
            self.assertIn('Fan duty/RPM', status['error'])
            self.assertEqual(port.writes[-1], b'0\n')


if __name__ == '__main__':
    unittest.main()
