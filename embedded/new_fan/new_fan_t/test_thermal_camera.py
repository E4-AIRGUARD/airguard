"""Camera scheduling/storage/failure regression tests without real hardware."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from thermal_camera import CameraRecorder


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / '사진' / 'run'
        self.cap = Mock()
        self.cap.isOpened.return_value = True
        self.cap.read.side_effect = self.frame
        self.cv = SimpleNamespace(VideoCapture=Mock(return_value=self.cap), CAP_DSHOW=1, CAP_ANY=0,
                                  CAP_PROP_FOURCC=6, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
                                  VideoWriter_fourcc=Mock(return_value=0), IMWRITE_JPEG_QUALITY=1,
                                  imencode=Mock(return_value=(True, SimpleNamespace(tobytes=lambda: b'jpeg-test'))))
        self.modules = patch.dict('sys.modules', {'cv2': self.cv})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    @staticmethod
    def frame():
        threading.Event().wait(0.005)
        return True, object()

    def wait_for(self, predicate):
        end = time.monotonic() + 2
        while not predicate() and time.monotonic() < end:
            threading.Event().wait(0.01)
        self.assertTrue(predicate())

    def test_images_are_timed_indexed_and_not_saved_before_begin(self):
        camera = CameraRecorder(self.directory, interval=0.05)
        try:
            camera.open()
            threading.Event().wait(0.03)
            self.assertEqual(camera.count, 0)
            camera.begin()
            self.wait_for(lambda: camera.count >= 3)
        finally:
            camera.close()
        entries = [json.loads(line) for line in (self.directory / 'images.jsonl').read_text().splitlines()]
        self.assertEqual(len(entries), camera.count)
        self.assertEqual(len(list(self.directory.glob('*.jpg'))), camera.count)
        elapsed = [entry['elapsed_sec'] for entry in entries]
        self.assertTrue(all(t >= 0 for t in elapsed))
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertGreater(elapsed[2] - elapsed[0], 0.07)
        for entry in entries:
            self.assertEqual((self.directory / entry['image']).read_bytes(), b'jpeg-test')
            self.assertIn('T', entry['Time'])
        self.cap.release.assert_called_once()

    def test_open_failure_releases_device(self):
        self.cap.isOpened.return_value = False
        camera = CameraRecorder(self.directory)
        with self.assertRaises(OSError):
            camera.open()
        with self.assertRaises(OSError):
            camera.close()
        self.cap.release.assert_called_once()

    def test_manual_exposure_is_opt_in_and_rejection_fails(self):
        self.cv.CAP_PROP_FPS = 5
        self.cv.CAP_PROP_AUTO_EXPOSURE = 21
        self.cv.CAP_PROP_EXPOSURE = 15
        self.cap.set.side_effect = lambda prop,value: prop != 21
        camera = CameraRecorder(self.directory,manual_exposure=-6)
        with self.assertRaisesRegex(OSError,'수동 노출'):
            camera.open()
        with self.assertRaises(OSError):
            camera.close()
        self.cap.release.assert_called_once()

    def test_requested_rotation_is_applied_before_encoding(self):
        rotated = object()
        self.cv.ROTATE_180 = 1
        self.cv.rotate = Mock(return_value=rotated)
        camera = CameraRecorder(self.directory, interval=0.02, rotation_deg=180)
        try:
            camera.open()
            camera.begin()
            self.wait_for(lambda: camera.count >= 1)
        finally:
            camera.close()
        self.assertTrue(self.cv.rotate.called)
        self.assertIs(self.cv.imencode.call_args.args[1], rotated)

    def test_read_failure_is_reported(self):
        self.cap.read.return_value = (False, None)
        self.cap.read.side_effect = None
        camera = CameraRecorder(self.directory)
        with self.assertRaises(OSError):
            camera.open()
        with self.assertRaises(OSError):
            camera.close()
        self.assertEqual(camera.count, 0)
        self.cap.release.assert_called_once()

    def test_encode_failure_does_not_report_saved_image(self):
        self.cv.imencode.return_value = (False, None)
        camera = CameraRecorder(self.directory, interval=0.05)
        camera.open()
        camera.begin()
        self.wait_for(lambda: camera.error is not None)
        with self.assertRaisesRegex(OSError, 'JPEG'):
            camera.close()
        self.assertEqual(camera.count, 0)
        self.assertEqual(list(self.directory.glob('*.jpg')), [])
        self.cap.release.assert_called_once()

    def test_existing_run_is_never_overwritten(self):
        self.directory.mkdir(parents=True)
        marker = self.directory / 'existing.jpg'
        marker.write_bytes(b'original')
        camera = CameraRecorder(self.directory)
        with self.assertRaises(FileExistsError):
            camera.open()
        camera.close()
        self.assertEqual(marker.read_bytes(), b'original')
        self.cv.VideoCapture.assert_not_called()

    def test_pause_stops_saving_and_resume_preserves_time_and_numbering(self):
        camera = CameraRecorder(self.directory, interval=0.02)
        try:
            camera.open()
            camera.begin()
            self.wait_for(lambda: camera.count >= 2)
            camera.pause()
            paused_count = camera.count
            start = camera._start
            reads = self.cap.read.call_count
            threading.Event().wait(0.08)
            self.assertEqual(camera.count, paused_count)
            self.assertGreater(self.cap.read.call_count, reads)
            camera.resume()
            self.wait_for(lambda: camera.count > paused_count)
            self.assertEqual(camera._start, start)
        finally:
            camera.close()
        entries = [json.loads(s) for s in (self.directory / 'images.jsonl').read_text().splitlines()]
        self.assertGreater(entries[paused_count]['elapsed_sec'] - entries[paused_count - 1]['elapsed_sec'], 0.07)


if __name__ == '__main__':
    unittest.main()
