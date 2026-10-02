# AI Messaging — desktop app (Windows)

The server's web UI in a native window, with what a browser tab on a
plain-http tailnet address cannot give you: **Windows notifications you
can click**, a **tray icon with an unread badge**, and a window that
**keeps watching from the tray** after you close it. Design: `docs/design.md` §10.9.

It is a *host*, not another client: it loads `http://<tailscale-ip>:8422/ui`
from the server, so the UI is always the one the server ships (no skew to
manage), and the page's identity, read checkpoints and settings live in
the app's own browser profile exactly as they would in a browser.

## Install from a build

Download `aim-desktop-windows.zip` from the latest *Desktop app (Windows)*
workflow run (or a `desktop-v*` release), unzip anywhere, run
`AI Messaging.exe`. First launch asks for the server address — the
tailnet IP the server binds to (`tailscale ip -4` on that machine). Needs
the WebView2 runtime, which Windows 10/11 already have.

**"Failed to resolve Python.Runtime.Loader.Initialize" at startup** means
Windows blocked the extracted files (the "Mark of the Web" every file
inherits from a downloaded zip): the .NET loader behind pywebview refuses
a blocked assembly. The app now unblocks its own files on start; if it
still fails, right-click the zip → Properties → *Unblock* and extract
again, or `Get-ChildItem <folder> -Recurse | Unblock-File` in PowerShell.

The address is validated the way the server validates its bind: a
Tailscale IP (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`), or a MagicDNS name
(`*.ts.net`, the way to use https via Tailscale Serve). Anything else is
refused with the reason. Loopback only with `AIM_ALLOW_LOOPBACK=1`, for
development.

## Run from source

```bash
cd desktop
pip install -e .[dev]        # on Windows this also installs windows-toasts
aim-desktop                  # or: python -m aim_desktop
aim-desktop --server http://100.101.102.103:8422 --hidden   # start in the tray
```

| Option / variable | Meaning |
|---|---|
| `--server URL` / `AIM_SERVER_URL` | Server origin; overrides the saved one (command line > environment > config file). |
| `--config PATH` / `AIM_DESKTOP_CONFIG` | Config file (default `%APPDATA%\aim-desktop\config.json`). |
| `--hidden` | Start in the tray with the window hidden. |
| `--debug` | Developer tools in the window, verbose log. |

Config file keys: `server_url`, `close_to_tray` (default `true`: the X
hides the window; *Quit* is in the tray menu), `start_hidden`, `width`,
`height`. A log is kept next to it (`desktop.log`), because a windowed
executable has no console to complain in.

On Linux/macOS the app runs for development with the notification
backend disabled (the page's settings say so); pywebview needs GTK or Qt
there.

## How it talks to the page

The app opens `/ui?desktop=<version>`; the page uses that to know it is
hosted and switches three things (nothing else changes):

- `desktopNotify` calls `pywebview.api.notify({title, body, chat_id,
  message_id})` instead of the browser's `Notification`. The host shows a
  Windows toast; a click brings the window up and calls
  `window.aimDesktopOpen(chat_id, message_id)`, which opens the chat and
  highlights the message. Both arguments are server-minted integers —
  nothing participant-written is ever interpolated into a script.
- `renderNotifBadge` also calls `pywebview.api.set_badge(n)` → tray badge.
- Settings read `pywebview.api.info()` so the notification switch says
  what backend the host has, and "keep checking in background" and
  "notifications" default **on** under the app (what it is for).

`tests/test_ui_contract.py` pins this contract against the server's
`ui.html`, so a change on either side fails there.

## Build

```bash
cd desktop
pip install -e .[build]
python build.py              # → dist/AI Messaging/AI Messaging.exe
```

Build on Windows: the WinRT modules only exist there. `.github/workflows/desktop.yml`
does it on `windows-latest` for every push touching `desktop/` or the UI.

## Tests

```bash
cd desktop && python -m pytest
```

They run on any platform: GUI libraries are imported lazily (pystray
opens a display at import time on Linux), the Windows backend is tested
against a stand-in for `windows-toasts`, and the real one in CI on Windows.
