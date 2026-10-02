"""Notification backends: the null one everywhere, the Windows one by contract."""

import sys
import types

import pytest

from aim_desktop import notify
from aim_desktop.notify import MAX_BODY, MAX_TITLE, NullNotifier, clip, make_notifier


def test_clip_bounds_and_marks_truncation():
    assert clip("short", 10) == "short"
    assert clip("  padded  ", 10) == "padded"
    assert clip(None, 10) == ""
    long = "x" * 500
    assert len(clip(long, MAX_BODY)) == MAX_BODY
    assert clip(long, MAX_TITLE).endswith("…")
    assert clip("a b c d e f", 5) == "a b…"


def test_null_notifier_records_and_reports_the_reason():
    n = NullNotifier("because")
    assert n.available is False
    assert n.reason == "because"
    n.show("t", "b", on_click=lambda: None)
    assert n.shown == [("t", "b")]


def test_other_platforms_get_the_null_backend():
    n = make_notifier(platform="linux")
    assert isinstance(n, NullNotifier)
    assert "Windows only" in n.reason
    assert make_notifier(platform="darwin").available is False


def test_windows_without_the_package_degrades_instead_of_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", None)   # import raises ImportError
    n = make_notifier(platform="win32")
    assert isinstance(n, NullNotifier)
    assert "windows-toasts" in n.reason


def test_windows_backend_failing_to_start_degrades_too(monkeypatch):
    class Boom(Exception):
        pass

    def explode(*_args, **_kwargs):
        raise Boom("no WinRT here")

    monkeypatch.setattr(notify, "WindowsToastNotifier", explode)
    n = make_notifier(platform="win32")
    assert isinstance(n, NullNotifier)
    assert "no WinRT here" in n.reason


@pytest.fixture
def fake_windows_toasts(monkeypatch):
    """A stand-in for the windows-toasts package with the surface we use."""
    module = types.ModuleType("windows_toasts")
    shown = []

    class Toast:  # pylint: disable=too-few-public-methods
        def __init__(self, text_fields=None, group=None, **_kw):
            self.text_fields = list(text_fields or [])
            self.group = group
            self.tag = "uuid"
            self.images = []
            self.on_activated = None

        def AddImage(self, image):  # pylint: disable=invalid-name
            self.images.append(image)

    class ToastDisplayImage:  # pylint: disable=too-few-public-methods
        @classmethod
        def fromPath(cls, path, **kw):  # pylint: disable=invalid-name
            return ("image", str(path), kw)

    class InteractableWindowsToaster:  # pylint: disable=too-few-public-methods
        def __init__(self, app_text, notifierAUMID=None):  # pylint: disable=invalid-name
            self.app_text = app_text
            self.aumid = notifierAUMID

        def show_toast(self, toast):
            shown.append(toast)

    class ToastImagePosition:  # pylint: disable=too-few-public-methods
        Inline = ""
        AppLogo = "appLogoOverride"

    module.ToastImagePosition = ToastImagePosition
    module.Toast = Toast
    module.ToastDisplayImage = ToastDisplayImage
    module.InteractableWindowsToaster = InteractableWindowsToaster
    monkeypatch.setitem(sys.modules, "windows_toasts", module)
    # No registry on this machine: the backend must cope and use the default identity.
    monkeypatch.setitem(sys.modules, "winreg", None)
    return shown


def test_windows_backend_contract(fake_windows_toasts, tmp_path):
    icon = tmp_path / "icon.ico"
    icon.write_bytes(b"\0")
    n = notify.WindowsToastNotifier(icon)
    assert n.available is True
    assert n._toaster.aumid is None            # winreg unavailable → default identity, not a crash

    clicks = []
    n.show("Nova · ops", "x" * 1000, on_click=lambda: clicks.append(1))
    toast, = fake_windows_toasts
    assert toast.text_fields[0] == "Nova · ops"
    assert len(toast.text_fields[1]) == MAX_BODY
    assert toast.tag == notify.TOAST_TAG      # a burst replaces the previous card (§10.8)
    assert toast.group == notify.TOAST_GROUP
    assert toast.images and toast.images[0][1] == str(icon)
    assert toast.images[0][2] == {"position": "appLogoOverride", "circleCrop": True}   # small, left — not full-width
    toast.on_activated(None)
    assert clicks == [1]

    # Without a click handler the toast is still shown, with no activation hook.
    n.show("t", "b")
    assert fake_windows_toasts[1].on_activated is None


def test_windows_backend_without_icon(fake_windows_toasts, tmp_path):
    n = notify.WindowsToastNotifier(tmp_path / "missing.ico")
    n.show("t", "b")
    assert fake_windows_toasts[0].images == []
