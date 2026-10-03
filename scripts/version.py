#!/usr/bin/env python3
"""One version for the whole project: server, MCP client, desktop app.

The three ship together and are checked against each other at runtime
(the client compares the server's declared version with its own, the
desktop app loads the UI the server serves), so they carry one number.
`VERSION` at the repository root is the source of truth; this script
copies it into every file that must repeat it, and CI refuses a tree
where any of them disagrees.

    python scripts/version.py check                 # all files equal VERSION?
    python scripts/version.py check --tag v0.15.1   # ...and the tag matches?
    python scripts/version.py set 0.15.2            # write VERSION and every file

Releasing is then: bump with `set`, commit, push, tag `v<VERSION>` and
push the tag — one tag publishes the .mcpb and the Windows build into
the same GitHub release.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# (file, pattern with two groups around the version). Multiline anchors so a
# dependency's version line can never be mistaken for ours.
PLACES: list[tuple[str, re.Pattern[str]]] = [
    ("server/pyproject.toml", re.compile(r'^(version = ")([^"]+)(")', re.M)),
    ("server/aim_server/__init__.py", re.compile(r'^(__version__ = ")([^"]+)(")', re.M)),
    ("mcp/pyproject.toml", re.compile(r'^(version = ")([^"]+)(")', re.M)),
    ("mcp/aim_mcp/__init__.py", re.compile(r'^(__version__ = ")([^"]+)(")', re.M)),
    ("mcp/manifest.json", re.compile(r'^(  "version": ")([^"]+)(",)', re.M)),
    # uv.lock records the project's own version next to its name.
    ("mcp/uv.lock", re.compile(r'(^name = "aim-mcp"\nversion = ")([^"]+)(")', re.M)),
    ("desktop/pyproject.toml", re.compile(r'^(version = ")([^"]+)(")', re.M)),
    ("desktop/aim_desktop/__init__.py", re.compile(r'^(__version__ = ")([^"]+)(")', re.M)),
]


def read_version() -> str:
    version = VERSION_FILE.read_text("utf-8").strip()
    if not SEMVER.match(version):
        sys.exit(f"VERSION holds {version!r}, not MAJOR.MINOR.PATCH")
    return version


def found_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for rel, pattern in PLACES:
        text = (ROOT / rel).read_text("utf-8")
        match = pattern.search(text)
        out[rel] = match.group(2) if match else None
    return out


def check(tag: str | None) -> int:
    version = read_version()
    problems = []
    for rel, found in found_versions().items():
        if found is None:
            problems.append(f"{rel}: no version line found (pattern drift?)")
        elif found != version:
            problems.append(f"{rel}: {found} (VERSION is {version})")
    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag!r} does not match VERSION {version!r} (expected v{version})")
    if problems:
        print("version mismatch:", file=sys.stderr)
        for problem in problems:
            print("  -", problem, file=sys.stderr)
        print(f"fix: python scripts/version.py set {version}", file=sys.stderr)
        return 1
    print(f"version {version} everywhere" + (f", tag {tag} OK" if tag else ""))
    return 0


def set_version(new: str) -> int:
    if not SEMVER.match(new):
        sys.exit(f"{new!r} is not MAJOR.MINOR.PATCH")
    VERSION_FILE.write_text(new + "\n", "utf-8")
    for rel, pattern in PLACES:
        path = ROOT / rel
        text = path.read_text("utf-8")
        updated, count = pattern.subn(lambda m: f"{m.group(1)}{new}{m.group(3)}", text, count=1)
        if count != 1:
            sys.exit(f"{rel}: version line not found; update the pattern in scripts/version.py")
        if updated != text:
            path.write_text(updated, "utf-8")
        print(f"{rel}: {new}")
    print(f"VERSION: {new}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="every file agrees with VERSION (and the tag, if given)")
    p_check.add_argument("--tag", help="a git tag that must equal v<VERSION>")
    p_set = sub.add_parser("set", help="write a new version into VERSION and every file")
    p_set.add_argument("version")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.tag)
    return set_version(args.version)


if __name__ == "__main__":
    sys.exit(main())
