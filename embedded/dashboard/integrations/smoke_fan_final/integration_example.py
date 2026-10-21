"""Team integration entry point. Defaults to real sensors with NO fan writes.

See ../INTEGRATION.md. The vendor fake loop is preserved as integration_example.original.py.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrated_control import main

if __name__ == '__main__':
    main()
