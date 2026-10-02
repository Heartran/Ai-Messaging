"""Native notifications — the reason this app exists (design §10.9).

The web UI already knows what is new; browsers just refuse to show a
notification for a page served over plain http on a tailnet IP. Here the
host process shows a Windows toast instead, and a click on it brings the
window to front and opens the message.

The backend is chosen at startup and never changes: `windows-toasts` on
Windows when it imports, a silent `NullNotifier` otherwise. The page asks
`info()` which one it got, so its settings can say so instead of letting
a switch silently do nothing.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger("aim_desktop.notify")

# Application User Model ID: what Windows files our toasts under. Registered
# per user in HKCU (no admin) so the toast carries our name and icon and the
# click comes back to this process instead of to cmd.exe.
APP_ID = "Heartran.AIMessaging.Desktop"
APP_NAME = "AI Messaging"
# One tag: a burst replaces the previous card instead of stacking, the same
# semantics the web UI gives the browser's Notification API (§10.8).
TOAST_TAG = "aim-news"
TOAST_GROUP = "aim"

MAX_TITLE = 120
MAX_BODY = 400

OnClick = Callable[[], None]


class Notifier(Protocol):
    available: bool
    reason: str

    def show(self, title: str, body: str, on_click: OnClick | None = None) -> None: ...


def clip(text: str, limit: int) -> str:
    """One line of defence against a wall of text in a toast: trim, bound."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class NullNotifier:
    """No native backend: say so, log, and let the page's bell do the work."""

    available = False

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.shown: list[tuple[str, str]] = []

    def show(self, title: str, body: str, on_click: OnClick | None = None) -> None:
        del on_click
        self.shown.append((title, body))
        log.info("notification (no native backend: %s): %s — %s", self.reason, title, body)


class WindowsToastNotifier:
    """Windows 10/11 toasts through WinRT, via the `windows-toasts` package."""

    available = True
    reason = "windows-toasts"

    def __init__(self, icon_path: Path | None = None, image_path: Path | None = None) -> None:
        """`icon_path` is the .ico the registry wants for the app identity;
        `image_path` is the picture on the toast itself. Keep them apart: an
        .ico handed to the toast is rendered from its smallest frame, 16 px
        blown up to a blur. The toast gets the full-size .png."""
        # pylint: disable=import-error  # Windows-only dependency
        from windows_toasts import InteractableWindowsToaster, Toast, ToastDisplayImage, ToastImagePosition

        self._toast_cls = Toast
        self._image_cls = ToastDisplayImage
        self._logo_position = ToastImagePosition.AppLogo
        self._icon = icon_path if icon_path and icon_path.exists() else None
        self._image = image_path if image_path and image_path.exists() else self._icon
        aumid = self._register_aumid()
        self._toaster = InteractableWindowsToaster(APP_NAME, notifierAUMID=aumid)

    def _register_aumid(self) -> str | None:
        """Register our AUMID under HKCU so toasts show our name and icon.

        Best effort: without it Windows still shows the toast, attributed
        to the default (cmd.exe) identity. Returns the AUMID to use.
        """
        try:
            import winreg  # pylint: disable=import-error

            key_path = f"SOFTWARE\\Classes\\AppUserModelId\\{APP_ID}"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                if self._icon is not None and self._icon.suffix.lower() == ".ico":
                    winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(self._icon.resolve()))
            return APP_ID
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.warning("could not register the notification identity (%s); using the default", exc)
            return None

    def show(self, title: str, body: str, on_click: OnClick | None = None) -> None:
        toast = self._toast_cls([clip(title, MAX_TITLE), clip(body, MAX_BODY)], group=TOAST_GROUP)
        toast.tag = TOAST_TAG
        if self._image is not None:
            try:
                # The small round logo on the left, like a chat avatar. The default
                # (inline) placement renders the image full-width under the text.
                toast.AddImage(self._image_cls.fromPath(
                    self._image, position=self._logo_position, circleCrop=True))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                log.debug("toast without icon: %s", exc)
        if on_click is not None:
            def activated(_args) -> None:
                try:
                    on_click()
                except Exception:  # pylint: disable=broad-exception-caught
                    log.exception("notification click handler failed")
            toast.on_activated = activated
        try:
            self._toaster.show_toast(toast)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.warning("could not show the notification: %s", exc)


def make_notifier(icon_path: Path | None = None, image_path: Path | None = None,
                  platform: str | None = None) -> Notifier:
    """The best backend this machine offers. Never raises: a missing backend
    is a degraded mode the page is told about, not a crash at startup."""
    plat = sys.platform if platform is None else platform
    if plat != "win32":
        return NullNotifier(f"native notifications are implemented for Windows only (this is {plat})")
    try:
        return WindowsToastNotifier(icon_path, image_path)
    except ImportError as exc:
        return NullNotifier(f"the windows-toasts package is not installed ({exc})")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.exception("notification backend failed to start")
        return NullNotifier(f"the Windows notification backend failed to start ({exc})")
