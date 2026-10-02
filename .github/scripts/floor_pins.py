"""Print the lowest declared version of every runtime dependency as an
exact pin, one per line: `pkg>=X` becomes `pkg==X`.

    pip install $(python .github/scripts/floor_pins.py server/pyproject.toml)

The declared floors are a promise to anyone installing on an older
environment, and a promise nobody tests is a guess: CI installs exactly
these and runs the suites, so a floor that is too low fails there instead
of on somebody's machine.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEP_LINE = re.compile(r'^\s*"([A-Za-z0-9_.-]+)\s*>=\s*([^",;\s]+)[^"]*",?\s*$')


def floors(pyproject: Path) -> list[str]:
    pins: list[str] = []
    inside = False
    for line in pyproject.read_text("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies = ["):
            inside = True
            continue
        if inside and stripped.startswith("]"):
            break
        if inside:
            match = DEP_LINE.match(line)
            if match:
                pins.append(f"{match.group(1)}=={match.group(2)}")
    if not pins:
        raise SystemExit(f"{pyproject}: no `pkg>=version` runtime dependencies found")
    return pins


if __name__ == "__main__":
    for path in sys.argv[1:] or ["pyproject.toml"]:
        print("\n".join(floors(Path(path))))
