"""The tray icon's badge drawing (pure Pillow) and its state machine, without pystray."""

from PIL import Image

from aim_desktop.tray import ICON_SIZE, Tray, badge_icon, badge_label, load_base_icon


def test_base_icon_is_the_project_icon_at_tray_size():
    icon = load_base_icon()
    assert icon.size == (ICON_SIZE, ICON_SIZE)
    assert icon.mode == "RGBA"


def test_badge_label():
    assert badge_label(1) == "1"
    assert badge_label(99) == "99"
    assert badge_label(100) == "99+"


def test_zero_returns_the_base_untouched():
    base = load_base_icon()
    assert badge_icon(base, 0) is base
    assert badge_icon(base, -3) is base


def test_badge_paints_the_corner_and_leaves_the_base_alone():
    base = load_base_icon()
    before = base.tobytes()
    badged = badge_icon(base, 7)
    assert badged is not base
    assert badged.size == base.size
    assert base.tobytes() == before
    # Bottom-right corner is now badge-coloured; the top-left is unchanged.
    w, h = badged.size
    r = int(h * 0.56) // 2
    assert badged.getpixel((w - r, h - 2))[:3] == (229, 72, 77)   # inside the circle, below the text
    assert badged.getpixel((2, 2)) == base.getpixel((2, 2))
    # And the text is there: some pixel in the badge is white.
    crop = badged.crop((w - int(h * 0.56), h - int(h * 0.56), w, h))
    assert any(px[:3] == (255, 255, 255) for px in crop.getdata())


def test_badge_handles_wide_labels():
    base = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    for count in (12, 99, 100, 12345):
        assert badge_icon(base, count).size == base.size


class FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = None
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_tray_state_without_a_real_icon():
    calls = []
    tray = Tray(lambda: calls.append("open"), lambda: calls.append("server"), lambda: calls.append("quit"))
    assert tray.title() == "AI Messaging"
    tray.set_badge(2)                      # no icon yet: remembered, nothing drawn
    assert tray.title() == "AI Messaging — 2 unread"

    fake = FakeIcon()
    tray._icon = fake
    tray.set_badge(2)                      # unchanged count: no redraw
    assert fake.icon is None
    tray.set_badge(5)
    assert fake.icon is not None and fake.title == "AI Messaging — 5 unread"
    tray.set_badge(0)
    assert fake.icon is tray._base and fake.title == "AI Messaging"
    tray.stop()
    assert fake.stopped and tray._icon is None
    tray.stop()                            # idempotent
