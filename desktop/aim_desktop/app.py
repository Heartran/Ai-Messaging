"""The window, the tray, and the glue between them.

`DesktopApp` is the `Host` the bridge talks to. It owns the one pywebview
window (pointed at the server's `/ui`, with `?desktop=<version>` so the
page knows who hosts it), the tray icon, and the notification backend.

pywebview and pystray are imported inside `run()` / `Tray.start()` only:
both want a display the moment they load, and nothing here needs them to
be tested.
"""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import __version__
from .bridge import DesktopBridge
from .config import ConfigError, DesktopConfig, resolve_server_url
from .notify import make_notifier
from .tray import ASSETS, Tray

log = logging.getLogger("aim_desktop.app")

UI_PATH = "/ui"
DESKTOP_QUERY = "desktop"   # the page reads this: it is the "you are hosted" signal
WINDOW_TITLE = "AI Messaging"


def ui_url(origin: str, version: str = __version__) -> str:
    """The page to load for a server origin, stamped with the host version."""
    return f"{origin}{UI_PATH}?{DESKTOP_QUERY}={quote(version, safe='')}"


def setup_html(current: str | None = None, error: str | None = None) -> str:
    """The first-run page: the server address, and nothing else to decide.

    Values are HTML-escaped: the current URL is ours, but the error text
    quotes whatever the person typed.
    """
    template = (ASSETS / "setup.html").read_text("utf-8")
    return (
        template
        .replace("__CURRENT__", html.escape(current or "", quote=True))
        .replace("__ERROR__", html.escape(error or ""))
        .replace("__VERSION__", html.escape(__version__))
    )


class DesktopApp:
    """One window, one tray icon, one notifier; implements bridge.Host."""

    def __init__(
        self,
        config: DesktopConfig,
        config_file: Path,
        cli_url: str | None = None,
        environ: dict[str, str] | None = None,
        debug: bool = False,
        start_hidden: bool = False,
        startup_error: str | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.config = config
        self.config_file = config_file
        self.debug = debug
        self.start_hidden = start_hidden or config.start_hidden
        self.startup_error = startup_error
        self.allow_loopback = env.get("AIM_ALLOW_LOOPBACK", "").strip() == "1"
        try:
            self.server_url, self.url_source = resolve_server_url(cli_url, config, env)
        except ConfigError as exc:
            # A bad address is shown on the setup page, never a silent death:
            # a windowed executable has no stderr anybody reads.
            self.server_url, self.url_source = None, "none"
            self.startup_error = str(exc)
        self.notifier = make_notifier(ASSETS / "icon.ico")
        self.bridge = DesktopBridge(self, self.notifier, config_file, self.allow_loopback)
        self.tray = Tray(self.show_window, self.ask_server, self.quit)
        self.window: Any = None
        self._quitting = False

    # ---------------------------------------------------------------- Host
    def show_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.show()
            window.restore()
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("could not bring the window to front")

    def run_js(self, script: str) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.evaluate_js(script)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("could not run a script in the page")

    def set_badge(self, count: int) -> None:
        self.tray.set_badge(count)

    def set_server_url(self, url: str) -> None:
        """Adopt a validated origin: remember it, load its UI."""
        self.config.server_url = url
        try:
            self.config.save(self.config_file)
        except OSError:
            log.exception("could not save %s", self.config_file)
        self.server_url = url
        self.url_source = "config file"
        log.info("server: %s", url)
        if self.window is not None:
            self.window.load_url(ui_url(url))

    # ---------------------------------------------------------- tray menu
    def ask_server(self) -> None:
        if self.window is not None:
            self.window.load_html(setup_html(self.server_url))
        self.show_window()

    def quit(self) -> None:
        self._quitting = True
        self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    # ------------------------------------------------------ window events
    def on_closing(self) -> bool | None:
        """The X hides the window into the tray (unless told otherwise or
        quitting). Returning False cancels the close in pywebview."""
        if self._quitting or not self.config.close_to_tray or self.window is None:
            return None
        self.window.hide()
        return False

    def on_started(self) -> None:
        """Runs once the GUI loop is up, in pywebview's worker thread."""
        try:
            self.tray.start()
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("tray icon unavailable; closing the window will quit")
            self.config.close_to_tray = False

    # ----------------------------------------------------------------- run
    def initial_page(self) -> tuple[str | None, str | None]:
        """`(url, html)`: the UI if a server is known, the setup page if not."""
        if self.server_url and not self.startup_error:
            return ui_url(self.server_url), None
        return None, setup_html(self.server_url, self.startup_error)

    def run(self) -> int:
        import webview  # pylint: disable=import-error  # wants a display at import time

        storage = self.config_file.parent / "webview-data"
        storage.mkdir(parents=True, exist_ok=True)
        url, page = self.initial_page()
        log.info("aim-desktop %s · server %s (%s) · notifications: %s",
                 __version__, self.server_url or "not configured", self.url_source, self.notifier.reason)
        self.window = webview.create_window(
            WINDOW_TITLE, url=url, html=page, js_api=self.bridge,
            width=self.config.width, height=self.config.height, min_size=(480, 360),
            text_select=True, hidden=self.start_hidden,
        )
        self.window.events.closing += self.on_closing
        # private_mode=False + storage_path: the page keeps its identity, read
        # checkpoints and settings in localStorage (§3) — they must survive a
        # restart exactly as they do in a browser profile.
        webview.start(
            self.on_started, private_mode=False, storage_path=str(storage),
            debug=self.debug, icon=str(ASSETS / "icon.png"),
        )
        self.tray.stop()
        return 0
