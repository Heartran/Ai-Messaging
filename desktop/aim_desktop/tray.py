"""The tray icon: the app's presence when its window is closed.

Closing the window hides it; the tray keeps the page polling and the
notifications flowing, and shows how much is unread as a badge drawn on
the icon. The drawing is pure Pillow (tested anywhere); the tray itself
is pystray, imported lazily because on Linux it opens a display at import
time — which no test or CI runner has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

ICON_SIZE = 64          # drawn large, the shell scales it down
BADGE_COLOR = (229, 72, 77, 255)
BADGE_TEXT = (255, 255, 255, 255)

ASSETS = Path(__file__).parent / "assets"


def load_base_icon(path: Path | None = None) -> Image.Image:
    source = path or ASSETS / "icon.png"
    return Image.open(source).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)


def badge_label(count: int) -> str:
    return "99+" if count > 99 else str(count)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)   # Pillow ≥ 10.1
    except TypeError:
        return ImageFont.load_default()


def badge_icon(base: Image.Image, count: int) -> Image.Image:
    """`base` with an unread badge in the bottom-right corner; `count <= 0`
    returns `base` itself, untouched."""
    if count <= 0:
        return base
    icon = base.copy()
    draw = ImageDraw.Draw(icon)
    label = badge_label(count)
    w, h = icon.size
    diameter = int(h * 0.56)
    font = _font(int(diameter * (0.7 if len(label) == 1 else 0.52)))
    left, top = w - diameter, h - diameter
    draw.ellipse((left, top, w - 1, h - 1), fill=BADGE_COLOR)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (left + (diameter - text_w) / 2 - bbox[0], top + (diameter - text_h) / 2 - bbox[1]),
        label, font=font, fill=BADGE_TEXT,
    )
    return icon


class Tray:
    """A pystray icon with Open / Server / Quit, and an updatable badge."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_server: Callable[[], None],
        on_quit: Callable[[], None],
        base: Image.Image | None = None,
    ) -> None:
        self._base = base or load_base_icon()
        self._on_open = on_open
        self._on_server = on_server
        self._on_quit = on_quit
        self._count = 0
        self._icon: Any = None

    def start(self) -> None:
        import pystray  # pylint: disable=import-error  # opens a display on import (Linux)

        menu = pystray.Menu(
            # pystray inspects the callable's arity: zero-argument callbacks are fine.
            pystray.MenuItem("Open AI Messaging", self._on_open, default=True),
            pystray.MenuItem("Server address…", self._on_server),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )
        self._icon = pystray.Icon("aim-desktop", self._base, self.title(), menu)
        self._icon.run_detached()

    def title(self) -> str:
        n = self._count
        return "AI Messaging" if not n else f"AI Messaging — {n} unread"

    def set_badge(self, count: int) -> None:
        count = max(0, int(count))
        if count == self._count:
            return
        self._count = count
        if self._icon is not None:
            self._icon.icon = badge_icon(self._base, count)
            self._icon.title = self.title()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            finally:
                self._icon = None
