import pytest

from groundhog_mcp.config import load_config
from groundhog_mcp.safety import BlockedURLError, check_url, is_blocked_ip


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1",
    "169.254.169.254", "0.0.0.0", "::1", "::ffff:127.0.0.1", "100.64.0.1",
])
def test_blocked_ips(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_allowed_ips(ip):
    assert is_blocked_ip(ip) is False


async def test_check_url_rejects_scheme():
    with pytest.raises(BlockedURLError):
        await check_url("file:///etc/passwd", load_config())


async def test_check_url_rejects_userinfo():
    with pytest.raises(BlockedURLError):
        await check_url("http://user:pass@example.com/", load_config())


async def test_check_url_blocks_loopback_host(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "true")
    with pytest.raises(BlockedURLError):
        await check_url("http://localhost/", load_config())
