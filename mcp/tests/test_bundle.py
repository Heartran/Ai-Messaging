"""The `.mcpb` bundle must not drift behind the sources (design §12.5).

The bundle is no longer committed: CI builds it with the official `mcpb`
CLI (.github/workflows/mcpb.yml) and checks what went in. What can still
drift is the manifest — its version against pyproject.toml, its tool list
against what the MCP server registers — and the ignore file that keeps
tests, caches and virtualenvs out of the pack. Both are pinned here.
"""

import json
import re
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]

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


def test_everything_the_bundle_ships_is_here():
    # mcpb pack takes the directory as it is: what the manifest and the
    # install need must exist next to it.
    manifest = json.loads((MCP_DIR / "manifest.json").read_text("utf-8"))
    for name in ("pyproject.toml", "uv.lock", "LICENSE", "user_config.example.json",
                 manifest["icon"], manifest["server"]["entry_point"]):
        assert (MCP_DIR / name).is_file(), name
    assert (MCP_DIR / "LICENSE").read_bytes() == (MCP_DIR.parent / "LICENSE").read_bytes(), (
        "mcp/LICENSE is a copy of the repo license and must stay identical")


def test_the_pack_leaves_out_what_must_never_ship():
    ignored = {line.strip() for line in (MCP_DIR / ".mcpbignore").read_text("utf-8").splitlines()
               if line.strip() and not line.startswith("#")}
    for pattern in (".venv/", "__pycache__/", "*.pyc", ".pytest_cache/", "tests/", "*.mcpb"):
        assert pattern in ignored, f"{pattern} missing from .mcpbignore"
    assert not (MCP_DIR / "aim.mcpb").exists(), "the bundle is a CI artifact, never committed"
