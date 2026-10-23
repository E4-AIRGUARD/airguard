"""사용: python scripts/run_control.py --help (설치 없이 실행)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from airguard_control.control import main

if __name__ == '__main__':
    main()
