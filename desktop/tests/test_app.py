"""The app's glue, with a fake window: URLs, the setup page, close-to-tray."""

from pathlib import Path

import pytest

from aim_desktop import __version__
from aim_desktop.app import DesktopApp, setup_html, ui_url
from aim_desktop.config import DesktopConfig


class FakeWindow:
    def __init__(self):
        self.log = []

    def show(self):
        self.log.append("show")

    def restore(self):
        self.log.append("restore")

    def hide(self):
        self.log.append("hide")

    def destroy(self):
        self.log.append("destroy")

    def load_url(self, url):
        self.log.append(("url", url))

    def load_html(self, page):
        self.log.append(("html", page))

    def evaluate_js(self, script):
        self.log.append(("js", script))


def make(tmp_path, config=None, cli_url=None, environ=None, **kw):
    environ = {} if environ is None else environ
    app = DesktopApp(config or DesktopConfig(), tmp_path / "config.json",
                     cli_url=cli_url, environ=environ, **kw)
    app.window = FakeWindow()
    return app


def test_ui_url_carries_the_host_version():
    assert ui_url("http://100.100.1.3:8422") == f"http://100.100.1.3:8422/ui?desktop={__version__}"
    assert ui_url("http://100.100.1.3:8422", "1.0 beta/2") == "http://100.100.1.3:8422/ui?desktop=1.0%20beta%2F2"


def test_setup_page_escapes_what_it_shows():
    page = setup_html('http://100.100.1.3:8422', '<b>"bad"</b> & more')
    assert 'value="http://100.100.1.3:8422"' in page
    assert "&lt;b&gt;&quot;bad&quot;&lt;/b&gt; &amp; more" in page
    assert '<b>"bad"</b>' not in page
    assert f"desktop v{__version__}" in page
    blank = setup_html()
    assert 'value=""' in blank


def test_without_a_server_the_setup_page_comes_first(tmp_path):
    app = make(tmp_path)
    url, page = app.initial_page()
    assert url is None and "Where is the server?" in page
    assert app.url_source == "none"


def test_with_a_server_the_ui_loads_straight_away(tmp_path):
    app = make(tmp_path, cli_url="100.100.1.3")
    url, page = app.initial_page()
    assert url == ui_url("http://100.100.1.3:8422") and page is None
    assert app.url_source == "command line"


def test_a_bad_address_becomes_the_setup_page_with_the_reason(tmp_path):
    app = make(tmp_path, environ={"AIM_SERVER_URL": "http://192.168.0.9:8422"})
    assert app.server_url is None
    url, page = app.initial_page()
    assert url is None
    assert "not a Tailscale address" in page
    assert "192.168.0.9" in page


def test_a_corrupt_config_is_reported_on_the_setup_page(tmp_path):
    app = make(tmp_path, startup_error="Cannot read config.json: boom")
    _, page = app.initial_page()
    assert "Cannot read config.json: boom" in page


def test_adopting_a_server_saves_and_navigates(tmp_path):
    app = make(tmp_path)
    app.set_server_url("http://100.100.1.3:8422")
    assert app.server_url == "http://100.100.1.3:8422"
    assert DesktopConfig.load(tmp_path / "config.json").server_url == "http://100.100.1.3:8422"
    assert app.window.log == [("url", ui_url("http://100.100.1.3:8422"))]
    # Through the bridge, the way the setup page does it.
    assert app.bridge.configure_server("100.100.9.9")["ok"] is True
    assert app.window.log[-1] == ("url", ui_url("http://100.100.9.9:8422"))


def test_close_hides_to_tray_by_default(tmp_path):
    app = make(tmp_path)
    assert app.on_closing() is False          # False = cancel the close (pywebview)
    assert app.window.log == ["hide"]


def test_close_really_closes_when_asked_or_quitting(tmp_path):
    app = make(tmp_path, config=DesktopConfig(close_to_tray=False))
    assert app.on_closing() is None
    assert not app.window.log

    app = make(tmp_path)
    app.quit()
    assert app.window.log == ["destroy"]
    assert app.on_closing() is None


def test_host_methods_drive_the_window(tmp_path):
    app = make(tmp_path)
    app.show_window()
    app.run_js("window.aimDesktopOpen && window.aimDesktopOpen(1, 2);")
    app.ask_server()
    assert app.window.log[:2] == ["show", "restore"]
    assert app.window.log[2] == ("js", "window.aimDesktopOpen && window.aimDesktopOpen(1, 2);")
    assert app.window.log[3][0] == "html" and "Where is the server?" in app.window.log[3][1]
    assert app.window.log[4:] == ["show", "restore"]


def test_badge_reaches_the_tray(tmp_path):
    app = make(tmp_path)
    app.bridge.set_badge(4)
    assert app.tray.title() == "AI Messaging — 4 unread"


def test_no_window_is_never_an_error(tmp_path):
    app = make(tmp_path)
    app.window = None
    app.show_window()
    app.run_js("1")
    app.ask_server()
    app.quit()
    assert app.on_closing() is None


def test_notifier_on_this_platform_is_known_to_the_page(tmp_path):
    app = make(tmp_path)
    info = app.bridge.info()
    assert info["config_path"] == str(tmp_path / "config.json")
    assert isinstance(info["notifications"], bool)


@pytest.mark.parametrize("flag, expected", [({}, False), ({"AIM_ALLOW_LOOPBACK": "1"}, True)])
def test_loopback_flag_is_read_once_for_the_whole_app(tmp_path, flag, expected):
    app = make(tmp_path, environ=flag)
    assert app.allow_loopback is expected
    assert app.bridge.configure_server("127.0.0.1")["ok"] is expected


def test_start_hidden_comes_from_flag_or_config(tmp_path):
    assert make(tmp_path).start_hidden is False
    assert make(tmp_path, start_hidden=True).start_hidden is True
    assert make(tmp_path, config=DesktopConfig(start_hidden=True)).start_hidden is True


def test_assets_are_packaged():
    from aim_desktop.tray import ASSETS
    assert isinstance(ASSETS, Path)
    assert (ASSETS / "icon.png").exists()
    assert (ASSETS / "icon.ico").exists()
    assert (ASSETS / "setup.html").exists()


def test_startup_failure_text_names_the_usual_cause(tmp_path):
    from aim_desktop.app import startup_failure_text
    text = startup_failure_text("Failed to resolve Python.Runtime.Loader.Initialize from x", tmp_path / "desktop.log")
    assert "Unblock" in text and "Unblock-File" in text
    assert str(tmp_path / "desktop.log") in text
    plain = startup_failure_text("something else", tmp_path / "desktop.log")
    assert "Unblock" not in plain and "something else" in plain


def test_window_icon_is_a_real_ico_on_windows():
    from aim_desktop.app import window_icon
    assert window_icon("win32").suffix == ".ico" and window_icon("win32").exists()
    assert window_icon("linux").suffix == ".png"
