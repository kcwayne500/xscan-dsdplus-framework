import pytest

from xscan.auth import AuthManager, RateLimited
from xscan.database import Database
from xscan.settings import SettingsStore


def test_per_channel_settings_replace_so_overrides_can_be_cleared(app_paths):
    settings = SettingsStore(app_paths)
    settings.update({"audio": {"per_channel": {"155.000": {"trigger_level": 0.01}}}})
    assert settings.section("audio")["per_channel"]
    settings.update({"audio": {"per_channel": {}}})
    assert settings.section("audio")["per_channel"] == {}


def test_default_network_route_is_local_only_with_loopback_backend(app_paths):
    server = SettingsStore(app_paths).section("server")
    assert server["host"] == "127.0.0.1"
    assert server["public_url"] == ""
    assert server["public_https_enabled"] is False
    assert server["lan_http_enabled"] is False
    assert "tailscale_url" not in server


def test_only_failed_logins_consume_rate_limit(app_paths):
    auth = AuthManager(app_paths, Database(app_paths))
    auth.setup("LongScannerPassword12")
    for _ in range(8):
        assert auth.verify("LongScannerPassword12", "local")
    for _ in range(5):
        assert not auth.verify("WrongPassword12", "attacker")
    with pytest.raises(RateLimited):
        auth.verify("WrongPassword12", "attacker")


def test_settings_are_transactional_and_restore_cannot_cross_hardware_lock(app_paths):
    settings = SettingsStore(app_paths)
    settings.update({"server": {"session_hours": 6}})
    settings.update({"server": {"session_hours": 9}})
    backup = settings.backups()[0]["name"]
    with pytest.raises(ValueError):
        settings.update({"audio": {"trigger_level": -1}})
    assert settings.section("audio")["trigger_level"] == 0.0021
    settings.update({"runtime": {"hardware_control_enabled": True}, "server": {"port": 8890}})
    restored = settings.restore(backup)
    assert restored["runtime"]["hardware_control_enabled"] is True
    assert restored["server"]["port"] == 8890
