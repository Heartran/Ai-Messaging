"""What the page may ask of the desktop host: `window.pywebview.api`.

pywebview exposes every public method of this object to the page as an
async function. The surface is deliberately tiny — the page already does
everything a chat client does; the host only adds what a browser tab on
a plain-http tailnet address cannot: a native notification, a tray badge,
a window to bring to front, and the one setting that is not the page's
(the server URL).

Everything that touches a real window or a real toast goes through the
`Host` and `Notifier` protocols, so this module is tested with fakes and
never imports a GUI toolkit.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Protocol

from . import __version__
from .config import ConfigError, validate_server_url
from .notify import Notifier

log = logging.getLogger("aim_desktop.bridge")


class Host(Protocol):
    def show_window(self) -> None: ...
    def run_js(self, script: str) -> None: ...
    def set_badge(self, count: int) -> None: ...
    def set_server_url(self, url: str) -> None: ...


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def open_message_script(chat_id: int, message_id: int | None) -> str:
    """The call that lands in the page when a notification is clicked.

    Both arguments are integers minted by the server, so the script is
    built from numbers only — nothing participant-written ever reaches
    a JavaScript string here (§2.3).
    """
    mid = "null" if message_id is None else str(int(message_id))
    return f"window.aimDesktopOpen && window.aimDesktopOpen({int(chat_id)}, {mid});"


class DesktopBridge:
    def __init__(
        self,
        host: Host,
        notifier: Notifier,
        config_path: Path,
        allow_loopback: bool = False,
    ) -> None:
        self._host = host
        self._notifier = notifier
        self._config_path = config_path
        self._allow_loopback = allow_loopback

    # -- what the page asks on load ---------------------------------------
    def info(self) -> dict[str, Any]:
        """Who the host is and what it can do, so the page's settings tell
        the truth instead of offering a switch that does nothing."""
        return {
            "app": "aim-desktop",
            "version": __version__,
            "platform": sys.platform,
            "notifications": bool(self._notifier.available),
            "notifications_backend": self._notifier.reason,
            "config_path": str(self._config_path),
        }

    # -- notifications -----------------------------------------------------
    def notify(self, payload: dict[str, Any] | None = None) -> bool:
        """Show a native notification; a click opens the message it is about.

        `payload`: {title, body, chat_id?, message_id?}. Returns whether a
        native backend showed it (False = the page's bell is all there is).
        """
        payload = payload if isinstance(payload, dict) else {}
        title = str(payload.get("title") or "AI Messaging")
        body = str(payload.get("body") or "")
        chat_id = _int_or_none(payload.get("chat_id"))
        message_id = _int_or_none(payload.get("message_id"))
        host = self._host

        def on_click() -> None:
            host.show_window()
            if chat_id is not None:
                host.run_js(open_message_script(chat_id, message_id))

        self._notifier.show(title, body, on_click)
        return bool(self._notifier.available)

    def set_badge(self, count: Any = 0) -> None:
        """Unread count for the tray icon (0 clears it)."""
        self._host.set_badge(max(0, _int_or_none(count) or 0))

    def focus(self) -> None:
        self._host.show_window()

    # -- the one setting that is the host's, not the page's ---------------
    def configure_server(self, url: str = "") -> dict[str, Any]:
        """Validate and adopt a server URL (from the first-run page or the
        tray). Refuses anything outside the tailnet with the reason."""
        try:
            origin = validate_server_url(url, allow_loopback=self._allow_loopback)
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        self._host.set_server_url(origin)
        return {"ok": True, "server_url": origin}
