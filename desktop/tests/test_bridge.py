"""The page ↔ host bridge, with a fake window and a fake notifier."""

from pathlib import Path

from aim_desktop import __version__
from aim_desktop.bridge import DesktopBridge, open_message_script
from aim_desktop.notify import NullNotifier


class FakeHost:
    def __init__(self):
        self.shown = 0
        self.scripts = []
        self.badges = []
        self.server_urls = []

    def show_window(self):
        self.shown += 1

    def run_js(self, script):
        self.scripts.append(script)

    def set_badge(self, count):
        self.badges.append(count)

    def set_server_url(self, url):
        self.server_urls.append(url)


class RecordingNotifier:
    available = True
    reason = "fake"

    def __init__(self):
        self.calls = []

    def show(self, title, body, on_click=None):
        self.calls.append((title, body, on_click))


def make(notifier=None, allow_loopback=False):
    host = FakeHost()
    notifier = notifier or RecordingNotifier()
    return host, notifier, DesktopBridge(host, notifier, Path("/tmp/aim/config.json"), allow_loopback)


def test_info_tells_the_page_what_it_got():
    _, _, bridge = make()
    info = bridge.info()
    assert info["app"] == "aim-desktop"
    assert info["version"] == __version__
    assert info["notifications"] is True
    assert info["notifications_backend"] == "fake"
    assert info["config_path"].endswith("config.json")

    _, _, degraded = make(NullNotifier("no backend here"))
    info = degraded.info()
    assert info["notifications"] is False
    assert "no backend here" in info["notifications_backend"]


def test_notify_shows_and_a_click_opens_the_message():
    host, notifier, bridge = make()
    shown = bridge.notify({"title": "Nova · ops", "body": "look at this", "chat_id": 7, "message_id": 123})
    assert shown is True
    (title, body, on_click), = notifier.calls
    assert (title, body) == ("Nova · ops", "look at this")
    assert host.shown == 0 and not host.scripts
    on_click()
    assert host.shown == 1
    assert host.scripts == ["window.aimDesktopOpen && window.aimDesktopOpen(7, 123);"]


def test_notify_without_a_message_only_brings_the_window_up():
    host, notifier, bridge = make()
    bridge.notify({"title": "3 new · 2 chats", "body": "a: b\nc: d"})
    notifier.calls[0][2]()
    assert host.shown == 1
    assert not host.scripts


def test_notify_coerces_ids_and_never_trusts_the_payload():
    host, notifier, bridge = make()
    bridge.notify({"title": "x", "body": "y", "chat_id": "12", "message_id": "nope"})
    notifier.calls[0][2]()
    assert host.scripts == ["window.aimDesktopOpen && window.aimDesktopOpen(12, null);"]
    # Garbage in: still a notification, still no crash.
    bridge.notify("not a dict")
    bridge.notify(None)
    assert [c[0] for c in notifier.calls[1:]] == ["AI Messaging", "AI Messaging"]


def test_notify_reports_a_missing_backend():
    _, _, bridge = make(NullNotifier("none"))
    assert bridge.notify({"title": "t", "body": "b"}) is False


def test_open_message_script_is_numbers_only():
    assert open_message_script(3, None) == "window.aimDesktopOpen && window.aimDesktopOpen(3, null);"
    assert open_message_script("4", "5") == "window.aimDesktopOpen && window.aimDesktopOpen(4, 5);"


def test_badge_is_forwarded_and_clamped():
    host, _, bridge = make()
    bridge.set_badge(3)
    bridge.set_badge("12")
    bridge.set_badge(-1)
    bridge.set_badge(None)
    bridge.set_badge("junk")
    assert host.badges == [3, 12, 0, 0, 0]


def test_focus_shows_the_window():
    host, _, bridge = make()
    bridge.focus()
    assert host.shown == 1


def test_configure_server_validates_then_adopts():
    host, _, bridge = make()
    result = bridge.configure_server("100.101.102.103")
    assert result == {"ok": True, "server_url": "http://100.101.102.103:8422"}
    assert host.server_urls == ["http://100.101.102.103:8422"]

    refused = bridge.configure_server("http://192.168.1.1:8422")
    assert refused["ok"] is False
    assert "not a Tailscale address" in refused["error"]
    assert host.server_urls == ["http://100.101.102.103:8422"]   # untouched


def test_configure_server_honours_the_loopback_flag():
    host, _, bridge = make(allow_loopback=True)
    assert bridge.configure_server("127.0.0.1")["ok"] is True
    assert host.server_urls == ["http://127.0.0.1:8422"]
    _, _, strict = make()
    assert strict.configure_server("127.0.0.1")["ok"] is False
