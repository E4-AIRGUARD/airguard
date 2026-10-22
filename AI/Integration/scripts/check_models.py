"""사용: python scripts/check_models.py (배포 모델 크기와 SHA-256 확인)."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from airguard_control.paths import model_directory


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['models']:
        path = (root/item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('모델 경로가 models 폴더를 벗어났습니다.')
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('모델 검증 실패: '+item['path'])
    return len(manifest['models'])


if __name__ == '__main__':
    print(f'{verify(model_directory())} models verified (SHA-256)')
