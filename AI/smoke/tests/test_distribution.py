"""폴더 단독 복사 및 모델 경로 이관을 검증한다. 장비·네트워크를 사용하지 않는다."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from airguard_control.paths import model_directory


class DistributionTests(unittest.TestCase):
    def test_model_manifest_matches_both_assets(self):
        manifest = json.loads((ROOT/'models/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['models']), 2)
        for item in manifest['models']:
            data = (ROOT/'models'/item['path']).read_bytes()
            self.assertEqual(len(data), item['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])

    def test_explicit_model_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {'AIRGUARD_MODEL_DIR': temporary}):
                self.assertEqual(model_directory(), Path(temporary).resolve())

    def test_project_runs_without_parent_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)/'standalone'
            shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(
                '__pycache__', '*.egg-info', 'build', 'dist', 'runtime'))
            env = dict(os.environ)
            env.pop('AIRGUARD_MODEL_DIR', None)
            env.pop('AIRGUARD_RUNTIME_DIR', None)
            env['PYTHONPATH'] = str(copied/'src')
            code = '''
from pathlib import Path
import sys
import airguard_control.control
from airguard_control.paths import model_path
from airguard_control.pm.smoke_fan import SmokeFan
from airguard_control.temperature.temperature_controller import TemperatureController
from airguard_control.storage import uploader
assert model_path('pm/model.json').is_file()
temperature = TemperatureController(model_path('temperature/lstm_temperature_trajectory_v6.pt'))
assert temperature.model.display_name == 'LSTM v6 (5s)'
smoke = SmokeFan()
assert smoke.step(20, now=0).intake in (35, 60, 100)
smoke.close()
assert all('embedded' not in str(getattr(module, '__file__', ''))
           for name, module in sys.modules.items() if name.startswith('airguard_control'))
'''
            commands = [[sys.executable, '-c', code],
                        [sys.executable, 'scripts/run_control.py', '--help'],
                        [sys.executable, 'scripts/upload_events.py', '--help'],
                        [sys.executable, 'integrated_control.py', '--help'],
                        [sys.executable, 'uploader.py', '--help']]
            for command in commands:
                result = subprocess.run(command, cwd=copied, env=env,
                                        capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
