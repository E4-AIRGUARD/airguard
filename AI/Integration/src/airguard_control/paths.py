"""소스 체크아웃과 설치 환경에서 모델·실행 결과 경로를 결정한다."""
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_checkout = (PROJECT_ROOT/'pyproject.toml').is_file()
RUNTIME_DIR = Path(os.environ.get('AIRGUARD_RUNTIME_DIR',
    str((PROJECT_ROOT if _checkout else Path.cwd())/'runtime'))).expanduser().resolve()


def model_directory():
    """환경변수, 체크아웃 models, 설치된 공용 데이터 순서로 선택한다."""
    override = os.environ.get('AIRGUARD_MODEL_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if _checkout:
        return PROJECT_ROOT/'models'
    return Path(sys.prefix)/'share/airguard-fan-control/models'


def model_path(relative):
    return model_directory()/relative
