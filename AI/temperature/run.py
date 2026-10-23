"""Run the standalone temperature controller from this folder or any working directory."""
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent / 'src'
sys.path.insert(0, str(SOURCE))

from main_runner import main


if __name__ == '__main__':
    main()
