"""Strip Windows' "Mark of the Web" from the bundle before the CLR loads it.

A zip downloaded with a browser and extracted by Explorer leaves a
`Zone.Identifier` alternate data stream on every file. Native DLLs load
regardless, but the .NET Framework refuses a marked assembly — and
pywebview's Windows backend is pythonnet, whose `Python.Runtime.dll` is
exactly that. The symptom is the opaque "Failed to resolve
Python.Runtime.Loader.Initialize" at startup. Deleting the stream is
allowed to the file's owner and is what the Explorer "Unblock" button
does, so the app does it for its own files on every start, best effort.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("aim_desktop.unblock")

ZONE_STREAM = ":Zone.Identifier"


def unblock_tree(root: Path, suffixes: tuple[str, ...] = (".dll", ".exe", ".pyd", ".json")) -> int:
    """Remove the Zone.Identifier stream from every matching file under
    `root`. Returns how many were unblocked; never raises."""
    if not root or not root.is_dir():
        return 0
    removed = 0
    for path in root.rglob("*"):
        if path.suffix.lower() not in suffixes or not path.is_file():
            continue
        try:
            os.remove(f"{path}{ZONE_STREAM}")
            removed += 1
        except FileNotFoundError:
            continue           # not marked: the usual case
        except OSError as exc:
            log.debug("could not unblock %s: %s", path, exc)
    if removed:
        log.info("removed the Mark of the Web from %d file(s) under %s", removed, root)
    return removed


def bundle_root() -> Path | None:
    """Where PyInstaller unpacked us, or None when running from source."""
    import sys

    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None
