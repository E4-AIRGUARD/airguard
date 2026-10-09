import unittest
import contextlib,io,json,tempfile
from pathlib import Path
from unittest.mock import patch
import measure_smoke_off90 as experiment
from measure_smoke_off90 import OffSmokeTiming

class TimingTests(unittest.TestCase):
    def test_removal_cue_after_60_seconds(self):
        cycle=OffSmokeTiming(10)
        self.assertIsNone(cycle.tick(69.99))
        self.assertEqual(cycle.tick(70),'remove_requested')
        self.assertIsNone(cycle.tick(71))

    def test_waits_for_physical_removal_even_after_90(self):
        cycle=OffSmokeTiming(0)
        cycle.tick(60)
        self.assertIsNone(cycle.tick(100))
        cycle.confirm_removal(101)
        self.assertIsNone(cycle.tick(130.99))
        self.assertEqual(cycle.tick(131),'complete')

    def test_rejects_early_and_duplicate_confirmation(self):
        cycle=OffSmokeTiming(0)
        with self.assertRaises(ValueError): cycle.confirm_removal(50)
        cycle.tick(60)
        cycle.confirm_removal(62)
        with self.assertRaises(ValueError): cycle.confirm_removal(65)

    def test_full_recording_includes_manual_removal_delay_without_starting_fans(self):
        clock=[0.0]
        command_queue=[None]
        class Port:
            is_open=False
            in_waiting=1
            writes=[]
            def open(self): self.is_open=True
            def close(self): self.is_open=False
            def reset_input_buffer(self): pass
            def write(self,value): self.writes.append(value)
            def read(self,size):
                clock[0]+=0.5
                t=clock[0]
                if t in (40,41,105): command_queue[0].put({40:'arm',41:'start',105:'removed'}[t])
                if t>150: command_queue[0].put('quit')
                if t%1==0:
                    pm=10 if t<42 else 1000
                    return f'PMS_SAMPLE,{int(t)},{int(t*1000)},10,{pm},1000\n'.encode()
                return b'DATA,100,0,0,0,0,25,40,1000,10,10,10,0,0\n'
        class Camera:
            count=0
            def __init__(self,*a,**kw): self.kw=kw
            def open(self): pass
            def begin(self): self.count=1
            def pause(self): pass
            def close(self): pass
            def check(self): pass
            def cadence_summary(self): return dict(ready=True,last_age_sec=0.01)
        port=Port()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(experiment,'__file__',str(Path(temp)/'script.py')), \
                 patch('serial.Serial',return_value=port), \
                 patch('thermal_camera.CameraRecorder',Camera), \
                 patch.object(experiment,'read_commands',lambda q:command_queue.__setitem__(0,q)), \
                 patch.object(experiment.time,'monotonic',side_effect=lambda:clock[0]), \
                 contextlib.redirect_stdout(io.StringIO()):
                result=experiment.main([])
            status=json.loads(next(Path(temp).rglob('status.json')).read_text())
            self.assertEqual(result,0)
            self.assertEqual(status['status'],'complete')
            self.assertGreater(status['removal_confirmation_delay_sec'],0)
            self.assertGreaterEqual(status['actual_post_removal_sec'],30)
            self.assertEqual(port.writes,[b'0\n',b'0\n'])
            self.assertTrue(status['stop_verified'])

if __name__=='__main__': unittest.main()
