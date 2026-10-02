"""PyInstaller entry script (see build.py). Equivalent to `python -m aim_desktop`."""

import sys

from aim_desktop.__main__ import run

if __name__ == "__main__":
    sys.exit(run())
