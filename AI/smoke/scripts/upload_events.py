"""사용: python scripts/upload_events.py --help (별도 큐 업로더)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from airguard_control.storage.uploader import main

if __name__ == '__main__':
    raise SystemExit(main())
