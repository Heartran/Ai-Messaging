"""The `.mcpb` bundle must not drift behind the sources (design §12.5).

The committed bundle sat at 0.6.4 while the code reached 0.7.0, with a
manifest that listed none of the newer tools. These tests make that
impossible to miss: the manifest's version follows pyproject.toml, its
tool list is exactly what the MCP server registers, and the bundle ships
the current sources and never a virtualenv.
"""

import json
import re
import zipfile
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
BUNDLE = MCP_DIR / "aim.mcpb"

pytestmark = pytest.mark.anyio


def pyproject_version() -> str:
    text = (MCP_DIR / "pyproject.toml").read_text("utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


async def registered_tool_names(monkeypatch) -> set[str]:
    monkeypatch.setenv("AIM_SERVER_URL", "http://aim.test")
    from aim_mcp.server import mcp

    return {tool.name for tool in await mcp.list_tools()}


async def test_the_manifest_matches_the_code(monkeypatch):
    manifest = json.loads((MCP_DIR / "manifest.json").read_text("utf-8"))

    assert manifest["version"] == pyproject_version()
    listed = [tool["name"] for tool in manifest["tools"]]
    assert len(listed) == len(set(listed)), "duplicate tool in manifest"
    assert set(listed) == await registered_tool_names(monkeypatch)
    for tool in manifest["tools"]:
        assert tool["description"].strip(), tool["name"]


def test_the_bundle_ships_the_current_sources_and_no_venv():
    with zipfile.ZipFile(BUNDLE) as archive:
        names = set(archive.namelist())
        assert not any(".venv" in name or "__pycache__" in name for name in names)
        for name in (
            "manifest.json", "pyproject.toml", "uv.lock", "icon.png", "LICENSE",
            "user_config.example.json", "aim_mcp/__main__.py",
        ):
            assert name in names, name
        for module in ("__init__", "__main__", "client", "server", "tools", "user_config"):
            bundled = archive.read(f"aim_mcp/{module}.py")
            assert bundled == (MCP_DIR / "aim_mcp" / f"{module}.py").read_bytes(), (
                f"aim_mcp/{module}.py in the bundle differs from the source: "
                "rebuild with python build_bundle.py"
            )
        assert archive.read("manifest.json") == (MCP_DIR / "manifest.json").read_bytes()
        assert archive.read("pyproject.toml") == (MCP_DIR / "pyproject.toml").read_bytes()
        assert archive.read("uv.lock") == (MCP_DIR / "uv.lock").read_bytes()
