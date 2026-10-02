"""Print the lowest declared version of every dependency, runtime and
dev extra alike, as an exact pin, one per line: `pkg>=X` becomes `pkg==X`.

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
    """Every `pkg>=X` in the runtime dependencies and in the dev extra.

    The dev floors are part of the promise too: a contributor on an older
    environment installs `.[dev]` and expects the suite to run. Today's
    lesson: fastapi 0.110 pins a starlette whose TestClient needs
    httpx < 0.28, so the server's dev floor httpx>=0.27 only holds when
    it is installed at its floor as well.
    """
    pins: list[str] = []
    inside = False
    for line in pyproject.read_text("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies = [") or stripped.startswith("dev = ["):
            inside = True
            continue
        if inside and stripped.startswith("]"):
            inside = False
            continue
        if inside:
            match = DEP_LINE.match(line.split("#", 1)[0])  # trailing comments are allowed
            if match:
                pins.append(f"{match.group(1)}=={match.group(2)}")
    if not pins:
        raise SystemExit(f"{pyproject}: no `pkg>=version` dependencies found")
    return pins


if __name__ == "__main__":
    for path in sys.argv[1:] or ["pyproject.toml"]:
        print("\n".join(floors(Path(path))))
