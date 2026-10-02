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
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import __version__
from .bridge import DesktopBridge
from .config import ConfigError, DesktopConfig, resolve_server_url
from .notify import make_notifier
from .tray import ASSETS, Tray
from .unblock import bundle_root, unblock_tree

log = logging.getLogger("aim_desktop.app")

UI_PATH = "/ui"
# While the window is hidden the host drives the page's chat-list read on
# its own clock: the engine throttles a hidden page's timers, a script run
# from outside is not throttled. Same relaxed pace as the page's own
# background poller (§10.8), never faster.
TRAY_POLL_SECONDS = 30
TICK_SCRIPT = "window.aimDesktopTick && window.aimDesktopTick();"
DESKTOP_QUERY = "desktop"   # the page reads this: it is the "you are hosted" signal
WINDOW_TITLE = "AI Messaging"


def ui_url(origin: str, version: str = __version__) -> str:
    """The page to load for a server origin, stamped with the host version."""
    return f"{origin}{UI_PATH}?{DESKTOP_QUERY}={quote(version, safe='')}"


def window_icon(platform: str | None = None) -> Path:
    """The icon file for the window toolkit. pywebview documents `icon` as
    GTK/Qt only, but its Windows backend hands the path to
    System.Drawing.Icon — which accepts an .ico and throws on a .png, inside
    the form constructor, with no window and no error to show for it."""
    plat = sys.platform if platform is None else platform
    return ASSETS / ("icon.ico" if plat == "win32" else "icon.png")


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


def startup_failure_text(reason: str, log_path: Path) -> str:
    text = f"AI Messaging could not start.\n\n{reason}\n\n"
    if "Python.Runtime" in reason or "Loader.Initialize" in reason:
        text += (
            "This is usually Windows blocking files that came from a downloaded zip "
            "(the \"Mark of the Web\"). Right-click the zip, Properties, tick Unblock, "
            "and extract it again — or run in PowerShell:\n"
            "    Get-ChildItem <folder> -Recurse | Unblock-File\n\n"
            "It also needs the .NET Framework 4.7.2+ and the WebView2 runtime, "
            "both part of Windows 10/11.\n\n"
        )
    text += f"Details are in {log_path}"
    return text


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
        self.notifier = make_notifier(ASSETS / "icon.ico", ASSETS / "icon.png")
        self.bridge = DesktopBridge(self, self.notifier, config_file, self.allow_loopback)
        self.tray = Tray(self.show_window, self.ask_server, self.quit)
        self.window: Any = None
        self._quitting = False
        self._hidden = self.start_hidden
        self._stop = threading.Event()

    # ---------------------------------------------------------------- Host
    def show_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.show()
            window.restore()
            self._hidden = False
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
        self._stop.set()
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
        self._hidden = True
        return False

    def tick(self) -> bool:
        """One beat of the tray clock: poke the page if the window is hidden.
        Returns whether it did."""
        if not self._hidden or self.window is None:
            return False
        self.run_js(TICK_SCRIPT)
        return True

    def _tick_loop(self) -> None:
        while not self._stop.wait(TRAY_POLL_SECONDS):
            self.tick()

    def on_started(self) -> None:
        """Runs once the GUI loop is up, in pywebview's worker thread."""
        try:
            self.tray.start()
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("tray icon unavailable; closing the window will quit")
            self.config.close_to_tray = False
        threading.Thread(target=self._tick_loop, name="aim-tray-clock", daemon=True).start()

    # ----------------------------------------------------------------- run
    def initial_page(self) -> tuple[str | None, str | None]:
        """`(url, html)`: the UI if a server is known, the setup page if not."""
        if self.server_url and not self.startup_error:
            return ui_url(self.server_url), None
        return None, setup_html(self.server_url, self.startup_error)

    def run(self) -> int:
        if sys.platform == "win32":
            # A zip extracted by Explorer leaves every file "blocked"; the .NET
            # loader behind pywebview then refuses its own runtime DLL.
            unblock_tree(bundle_root())
        try:
            import webview  # pylint: disable=import-error  # wants a display at import time
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return self._fail_to_start(f"The window toolkit could not be loaded: {exc}")

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
        try:
            webview.start(
                self.on_started, private_mode=False, storage_path=str(storage),
                debug=self.debug, icon=str(window_icon()),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return self._fail_to_start(f"The window could not be created: {exc}")
        finally:
            self._stop.set()
            self.tray.stop()
        return 0

    def _fail_to_start(self, reason: str) -> int:
        """A windowed executable has nowhere to print: log it and, on Windows,
        say it in a message box with the one fix that usually applies."""
        log.exception("startup failed: %s", reason)
        text = startup_failure_text(reason, self.config_file.parent / "desktop.log")
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, text, WINDOW_TITLE, 0x10)  # MB_ICONERROR
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        print(text, file=sys.stderr)
        return 1
