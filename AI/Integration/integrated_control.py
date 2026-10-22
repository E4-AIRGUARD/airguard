"""이전 실행 명령을 유지한다. 권장: python scripts/run_control.py --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from airguard_control.control import main

if __name__ == '__main__':
    main()
