"""The desktop config: tailnet-only server URLs, file round-trip, precedence."""

from pathlib import Path

import pytest

from aim_desktop.config import (
    ConfigError,
    DesktopConfig,
    config_path,
    resolve_server_url,
    validate_server_url,
)


# ---------------------------------------------------------------- URLs
@pytest.mark.parametrize("given, expected", [
    ("http://100.101.102.103:8422", "http://100.101.102.103:8422"),
    ("http://100.101.102.103:8422/", "http://100.101.102.103:8422"),
    ("http://100.101.102.103:8422/ui", "http://100.101.102.103:8422"),   # pasted from the browser
    ("http://100.101.102.103", "http://100.101.102.103:8422"),           # default port
    ("100.101.102.103", "http://100.101.102.103:8422"),                  # bare address
    ("  100.127.0.1:9000 ", "http://100.127.0.1:9000"),
    ("http://[fd7a:115c:a1e0::1]:8422", "http://[fd7a:115c:a1e0::1]:8422"),
    ("https://omen.tail1234.ts.net", "https://omen.tail1234.ts.net:8422"),   # MagicDNS, Tailscale Serve
    ("https://omen.TAIL1234.TS.NET:443/ui", "https://omen.tail1234.ts.net:443"),   # hostnames are case-insensitive
])
def test_tailnet_urls_are_normalized(given, expected):
    assert validate_server_url(given) == expected


@pytest.mark.parametrize("given, fragment", [
    ("", "No server URL"),
    ("http://192.168.1.10:8422", "not a Tailscale address"),       # LAN
    ("http://8.8.8.8:8422", "not a Tailscale address"),            # public
    ("http://100.63.255.255:8422", "not a Tailscale address"),     # just outside the CGNAT range
    ("http://0.0.0.0:8422", "not a Tailscale address"),
    ("http://messaging.example.com:8422", "not a tailnet address"),  # hostname that is not MagicDNS
    ("http://127.0.0.1:8422", "loopback"),
    ("ftp://100.101.102.103:8422", "only http and https"),
    ("http://user:pw@100.101.102.103:8422", "credentials"),
    ("http://100.101.102.103:8422/api", "origin only"),
    ("http://100.101.102.103:8422/ui?x=1", "origin only"),
    ("http://100.101.102.103:notaport", "port"),
    ("http://:8422", "no host"),
])
def test_everything_else_is_refused_with_a_reason(given, fragment):
    with pytest.raises(ConfigError) as excinfo:
        validate_server_url(given)
    assert fragment in str(excinfo.value)


def test_loopback_only_when_explicitly_allowed():
    with pytest.raises(ConfigError):
        validate_server_url("http://127.0.0.1:8422")
    assert validate_server_url("http://127.0.0.1:8422", allow_loopback=True) == "http://127.0.0.1:8422"
    # A hostname is not an address: even "localhost" is refused as a name.
    with pytest.raises(ConfigError):
        validate_server_url("http://localhost:8422", allow_loopback=True)


# ----------------------------------------------------------- the file
def test_config_path_follows_the_platform(tmp_path):
    win = config_path({"APPDATA": r"C:\Users\fede\AppData\Roaming"}, platform="win32")
    assert win.as_posix().endswith("aim-desktop/config.json")
    assert "AppData" in str(win)
    other = config_path({}, platform="linux")
    assert other == Path.home() / ".aim" / "desktop.json"
    override = config_path({"AIM_DESKTOP_CONFIG": str(tmp_path / "x.json")}, platform="win32")
    assert override == tmp_path / "x.json"


def test_missing_file_is_the_defaults(tmp_path):
    config = DesktopConfig.load(tmp_path / "nope.json")
    assert config == DesktopConfig()
    assert config.server_url is None
    assert config.close_to_tray is True


def test_round_trip_and_unknown_keys_ignored(tmp_path):
    path = tmp_path / "sub" / "config.json"
    config = DesktopConfig(server_url="http://100.100.1.3:8422", close_to_tray=False, width=900, height=600)
    config.save(path)
    assert path.exists()
    path.write_text(path.read_text("utf-8").replace("{", '{"future_key": 1, ', 1), "utf-8")
    loaded = DesktopConfig.load(path)
    assert loaded == config


def test_tiny_window_sizes_are_clamped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"width": 10, "height": 10}', "utf-8")
    loaded = DesktopConfig.load(path)
    assert (loaded.width, loaded.height) == (480, 360)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", "42"])
def test_corrupt_file_is_an_explicit_error(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, "utf-8")
    with pytest.raises(ConfigError) as excinfo:
        DesktopConfig.load(path)
    assert str(path) in str(excinfo.value)


# --------------------------------------------------------- precedence
def test_command_line_beats_environment_beats_file():
    config = DesktopConfig(server_url="http://100.64.0.3:8422")
    env = {"AIM_SERVER_URL": "100.64.0.2"}
    assert resolve_server_url("100.64.0.1", config, env) == ("http://100.64.0.1:8422", "command line")
    assert resolve_server_url(None, config, env) == ("http://100.64.0.2:8422", "AIM_SERVER_URL")
    assert resolve_server_url(None, config, {}) == ("http://100.64.0.3:8422", "config file")
    assert resolve_server_url(None, DesktopConfig(), {}) == (None, "none")
    assert resolve_server_url("   ", DesktopConfig(), {"AIM_SERVER_URL": " "}) == (None, "none")


def test_precedence_validates_the_winner_only():
    # A bad value that wins is an error; a bad value that loses is irrelevant.
    config = DesktopConfig(server_url="http://192.168.0.1:8422")
    assert resolve_server_url("100.64.0.1", config, {})[0] == "http://100.64.0.1:8422"
    with pytest.raises(ConfigError):
        resolve_server_url(None, config, {})


def test_loopback_flag_flows_through_resolution():
    config = DesktopConfig(server_url="http://127.0.0.1:8422")
    with pytest.raises(ConfigError):
        resolve_server_url(None, config, {})
    assert resolve_server_url(None, config, {"AIM_ALLOW_LOOPBACK": "1"})[0] == "http://127.0.0.1:8422"
