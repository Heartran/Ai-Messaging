"""AI Messaging — desktop app for Windows.

A native window around the server's own web UI (`/ui`), plus what a
browser tab on a plain-http tailnet address cannot give a person: native
Windows notifications you can click, a tray icon with an unread badge,
and a window that keeps watching from the tray after it is closed. The
page stays the single UI (design §10.9): the app hosts it, it does not
reimplement it.
"""

__version__ = "0.15.1"
