"""Desktop configuration: where the server is, how the window behaves.

One JSON file in the per-user application-data directory
(`%APPDATA%\\aim-desktop\\config.json` on Windows, `~/.aim/desktop.json`
elsewhere; `AIM_DESKTOP_CONFIG` overrides the path). The server URL is
the only thing the app cannot guess; everything else has a default.

The server refuses to bind outside the tailnet (server/config.py); this
client refuses to *connect* outside it for the same reason (design §2.1):
a URL pointing at a LAN or public address is a misconfiguration that
would otherwise fail later and less clearly.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")  # CGNAT range
TAILSCALE_IPV6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
MAGICDNS_SUFFIX = ".ts.net"
DEFAULT_PORT = 8422

APP_DIR_NAME = "aim-desktop"


class ConfigError(ValueError):
    """Configuration the app refuses to run with — the message says why."""


def config_path(environ: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """Where the configuration file lives for this user."""
    env = os.environ if environ is None else environ
    override = env.get("AIM_DESKTOP_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    plat = sys.platform if platform is None else platform
    appdata = env.get("APPDATA", "").strip()
    if plat == "win32" and appdata:
        return Path(appdata) / APP_DIR_NAME / "config.json"
    return Path.home() / ".aim" / "desktop.json"


def validate_server_url(url: str, allow_loopback: bool = False) -> str:
    """Normalize a server URL and refuse anything outside the tailnet.

    Accepts `http://100.x.y.z:8422`, a bare `100.x.y.z` (http and the
    default port are assumed), an IPv6 tailnet address in brackets, or a
    MagicDNS name (`*.ts.net`, the only way to reach the server over https
    via Tailscale Serve). Tolerates a pasted `/ui` path. Returns the origin
    (`scheme://host:port`) with no trailing slash.
    """
    raw = (url or "").strip()
    if not raw:
        raise ConfigError(
            "No server URL. Put the central server's tailnet address in the "
            "settings (run `tailscale ip -4` on the server machine), e.g. "
            f"http://100.101.102.103:{DEFAULT_PORT}."
        )
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise ConfigError(f"{url!r} is not a valid URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise ConfigError(f"{url!r}: only http and https are supported, not {parts.scheme!r}.")
    if parts.username or parts.password:
        raise ConfigError(f"{url!r}: credentials in the URL are not supported (the server has none).")
    path = parts.path.rstrip("/")
    if path not in ("", "/ui") or parts.query or parts.fragment:
        raise ConfigError(
            f"{url!r}: give the server's origin only, e.g. http://100.101.102.103:{DEFAULT_PORT} "
            "(the /ui path is tolerated, nothing else)."
        )
    host = parts.hostname
    if not host:
        raise ConfigError(f"{url!r} has no host.")
    try:
        port = parts.port
    except ValueError:
        raise ConfigError(f"{url!r}: the port is not a number.") from None
    port = DEFAULT_PORT if port is None else port

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is None:
        if not host.lower().endswith(MAGICDNS_SUFFIX):
            raise ConfigError(
                f"{host!r} is not a tailnet address. Use the server's Tailscale IP "
                f"(100.64.0.0/10 or {TAILSCALE_IPV6}) or its MagicDNS name "
                f"(*{MAGICDNS_SUFFIX}). The tailnet perimeter is the security model: "
                "this app does not connect anywhere else."
            )
    elif addr.is_loopback:
        if not allow_loopback:
            raise ConfigError(
                f"{host!r} is a loopback address. For local development against a "
                "server started with AIM_ALLOW_LOOPBACK=1, set AIM_ALLOW_LOOPBACK=1 "
                "for this app too. Never on a real deployment."
            )
    elif not (
        (isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_IPV4)
        or (isinstance(addr, ipaddress.IPv6Address) and addr in TAILSCALE_IPV6)
    ):
        raise ConfigError(
            f"{host!r} is not a Tailscale address (expected an IP in {TAILSCALE_IPV4} "
            f"or {TAILSCALE_IPV6}). The tailnet perimeter is the security model: "
            "this app does not connect anywhere else."
        )

    host_part = f"[{host}]" if addr is not None and addr.version == 6 else host
    return f"{parts.scheme}://{host_part}:{port}"


@dataclass
class DesktopConfig:
    """Everything the app remembers between launches, besides the page's own state."""

    server_url: str | None = None
    close_to_tray: bool = True   # the X hides the window; the tray keeps watching
    start_hidden: bool = False   # launch straight into the tray
    width: int = 1180
    height: int = 780

    @classmethod
    def load(cls, path: Path) -> DesktopConfig:
        """Read the file if it exists; a missing file is simply the defaults."""
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read {path}: {exc}") from None
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} does not hold a JSON object.")
        known = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in raw.items() if k in known})
        config.width = max(480, int(config.width))
        config.height = max(360, int(config.height))
        return config

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", "utf-8")


def resolve_server_url(
    cli_url: str | None,
    config: DesktopConfig,
    environ: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Pick the server URL: command line, then AIM_SERVER_URL, then the file.

    Returns `(url, source)`; the URL is validated, and `None` with source
    `"none"` means the app must ask the person before it can do anything.
    """
    env = os.environ if environ is None else environ
    allow_loopback = env.get("AIM_ALLOW_LOOPBACK", "").strip() == "1"
    for candidate, source in (
        (cli_url, "command line"),
        (env.get("AIM_SERVER_URL"), "AIM_SERVER_URL"),
        (config.server_url, "config file"),
    ):
        if candidate and candidate.strip():
            return validate_server_url(candidate, allow_loopback=allow_loopback), source
    return None, "none"
