"""이전 업로드 명령을 유지한다. 권장: python scripts/upload_events.py --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from airguard_control.storage.uploader import main

if __name__ == '__main__':
    raise SystemExit(main())
