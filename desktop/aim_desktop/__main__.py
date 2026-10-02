"""Entrypoint: `python -m aim_desktop` (or the `aim-desktop` console script,
or the packaged `AI Messaging.exe`).

A windowed executable has no console, so configuration errors never go
only to stderr: a missing or bad server address becomes the setup page,
and everything is also logged to `desktop.log` next to the config file.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import ConfigError, DesktopConfig, config_path

log = logging.getLogger("aim_desktop")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aim-desktop",
        description="AI Messaging desktop app: the web UI in a window, with native notifications.",
    )
    parser.add_argument("--server", metavar="URL",
                        help="central server origin, e.g. http://100.101.102.103:8422 "
                             "(overrides AIM_SERVER_URL and the config file)")
    parser.add_argument("--config", metavar="PATH", help="config file (default: per-user app data)")
    parser.add_argument("--hidden", action="store_true", help="start in the tray, window hidden")
    parser.add_argument("--debug", action="store_true", help="developer tools and verbose logs")
    parser.add_argument("--version", action="version", version=f"aim-desktop {__version__}")
    return parser


def setup_logging(directory: Path, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(directory / "desktop.log", maxBytes=1_000_000, backupCount=2,
                                           encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        log.warning("no log file (%s); logging to stderr only", exc)


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.config).expanduser() if args.config else config_path()
    setup_logging(path.parent, args.debug)

    startup_error = None
    try:
        config = DesktopConfig.load(path)
    except ConfigError as exc:
        log.error("%s", exc)
        config, startup_error = DesktopConfig(), str(exc)

    from .app import DesktopApp  # imports nothing GUI-related until run()

    app = DesktopApp(config, path, cli_url=args.server, debug=args.debug,
                     start_hidden=args.hidden, startup_error=startup_error)
    if app.startup_error:
        print(f"Configuration error: {app.startup_error}", file=sys.stderr)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(run())
