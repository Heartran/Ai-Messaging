"""Build the Windows executable: `python build.py` from `desktop/`.

Produces `dist/AI Messaging/AI Messaging.exe` (one directory, no
installer) with PyInstaller. Run it on Windows — the result is a Windows
binary, and the WinRT notification modules only exist there. The
`desktop.yml` workflow does exactly this on every push and publishes the
zip as a build artifact (and as a release on `desktop-v*` tags).

Nothing machine-specific goes in: the server address is asked on first
run and stored per user (design §12.1).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "aim_desktop" / "assets"
APP_NAME = "AI Messaging"


def pyinstaller_args(platform: str = sys.platform) -> list[str]:
    args = [
        str(HERE / "launcher.py"),
        "--name", APP_NAME,
        "--noconfirm", "--clean",
        "--windowed",
        "--icon", str(ASSETS / "icon.ico"),
        # The package's non-Python files (icons, the setup page).
        "--collect-data", "aim_desktop",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]
    if platform == "win32":
        # windows-toasts imports its WinRT projections at module level, but the
        # projections are namespace packages spread over several wheels: name
        # them so the analysis cannot miss one.
        args += ["--collect-all", "windows_toasts"]
        for module in (
            "winrt.system",
            "winrt.windows.data.xml.dom",
            "winrt.windows.foundation",
            "winrt.windows.foundation.collections",
            "winrt.windows.ui.notifications",
        ):
            args += ["--hidden-import", module]
    return args


def main() -> int:
    import PyInstaller.__main__  # pylint: disable=import-error

    os.chdir(HERE)
    PyInstaller.__main__.run(pyinstaller_args())
    print(f"built: {HERE / 'dist' / APP_NAME / (APP_NAME + '.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
