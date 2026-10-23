"""Run integration tests in separate processes; no Jetson or serial device is used."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for name in ('temperature_api', 'sensor_bridge', 'integrated_control',
             'integration_store', 'dashboard_lease', 'lease_proxy', 'pm_recovery', 'vision_recovery', 'recovery_api'):
    subprocess.run([sys.executable, str(ROOT/'tests'/f'{name}.py')], cwd=ROOT, check=True)
print('All dashboard Python tests passed.', flush=True)
