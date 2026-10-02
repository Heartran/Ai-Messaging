"""Build the `.mcpb` extension bundle from the sources in this directory.

    python build_bundle.py            # writes aim.mcpb next to this file

The bundle is a zip of exactly what `uv run` needs on the target machine:
the package sources, pyproject.toml, uv.lock, manifest.json, the icon,
the license and the user_config example. Never a `.venv` (design §12.5):
virtualenvs are not relocatable, so the environment is created on the
target machine on first start.

The manifest is the source of truth for the extension's metadata, and it
is checked against the code by the test suite: its version must match
pyproject.toml and its `tools` list must name exactly the tools the MCP
server registers, so a bundle can no longer drift behind the sources
unnoticed.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "aim.mcpb"

# (archive name, source path); sources are relative to this directory
# unless absolute.
CONTENTS = [
    ("aim_mcp/__init__.py", HERE / "aim_mcp" / "__init__.py"),
    ("aim_mcp/__main__.py", HERE / "aim_mcp" / "__main__.py"),
    ("aim_mcp/client.py", HERE / "aim_mcp" / "client.py"),
    ("aim_mcp/server.py", HERE / "aim_mcp" / "server.py"),
    ("aim_mcp/tools.py", HERE / "aim_mcp" / "tools.py"),
    ("aim_mcp/user_config.py", HERE / "aim_mcp" / "user_config.py"),
    ("pyproject.toml", HERE / "pyproject.toml"),
    ("uv.lock", HERE / "uv.lock"),
    ("manifest.json", HERE / "manifest.json"),
    ("icon.png", HERE / "icon.png"),
    ("LICENSE", HERE.parent / "LICENSE"),
    ("user_config.example.json", HERE / "user_config.example.json"),
]


def pyproject_version() -> str:
    text = (HERE / "pyproject.toml").read_text("utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml: no version line")
    return match.group(1)


def check_manifest() -> dict:
    manifest = json.loads((HERE / "manifest.json").read_text("utf-8"))
    version = pyproject_version()
    if manifest.get("version") != version:
        raise SystemExit(
            f"manifest.json says version {manifest.get('version')!r} but "
            f"pyproject.toml says {version!r}: align them before building."
        )
    return manifest


def build(target: Path = BUNDLE) -> Path:
    check_manifest()
    missing = [str(src) for _, src in CONTENTS if not src.is_file()]
    if missing:
        raise SystemExit(f"missing bundle inputs: {missing}")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, src in CONTENTS:
            archive.write(src, name)
    return target


if __name__ == "__main__":
    path = build()
    size = path.stat().st_size
    print(f"built {path} ({size} bytes, {len(CONTENTS)} files)")
    sys.exit(0)
