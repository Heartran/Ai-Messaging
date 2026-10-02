"""The contract between this host and the server's web UI.

The page is the server's (server/aim_server/static/ui.html); this app
only loads it. These checks pin the three things both sides must agree
on — the `?desktop=` signal, the `pywebview.api` calls the page makes,
and the `aimDesktopOpen` hook the host calls back — so a change on one
side fails here instead of in somebody's tray.
"""

from pathlib import Path

import pytest

from aim_desktop.app import DESKTOP_QUERY
from aim_desktop.bridge import DesktopBridge

UI = Path(__file__).resolve().parents[2] / "server" / "aim_server" / "static" / "ui.html"


@pytest.fixture(scope="module")
def page() -> str:
    assert UI.exists(), f"web UI not found at {UI}"
    return UI.read_text("utf-8")


def test_page_reads_the_desktop_signal(page):
    assert f'.get("{DESKTOP_QUERY}")' in page


def test_page_calls_only_methods_the_bridge_exposes(page):
    exposed = {name for name in dir(DesktopBridge) if not name.startswith("_")}
    import re
    called = set(re.findall(r"\bapi\.(\w+)\(", page))
    assert called, "the page never talks to the bridge?"
    assert called <= exposed, f"page calls {called - exposed} which the bridge does not expose"
    for required in ("notify", "set_badge", "info"):
        assert required in called


def test_page_installs_the_click_hook(page):
    assert "window.aimDesktopOpen = " in page


def test_page_tells_the_person_about_native_notifications(page):
    assert "Windows notifications" in page
