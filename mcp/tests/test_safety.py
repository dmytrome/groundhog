import pytest

from groundhog_mcp.config import load_config
from groundhog_mcp.safety import BlockedURLError, check_url, is_blocked_ip, safe_detail


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
        "100.64.0.1",
    ],
)
def test_blocked_ips(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_allowed_ips(ip):
    assert is_blocked_ip(ip) is False


async def test_check_url_rejects_scheme():
    with pytest.raises(BlockedURLError):
        await check_url("file:///etc/passwd", load_config())


@pytest.mark.parametrize("userinfo", ["user:pass", "user", ":pass"])
async def test_check_url_rejects_any_credential_in_the_url(userinfo):
    with pytest.raises(BlockedURLError, match="credentials"):
        await check_url(f"http://{userinfo}@example.com/", load_config())


async def test_check_url_lets_a_public_address_through(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "true")

    allowed = await check_url("http://93.184.216.34/page", load_config())

    assert allowed is None


async def test_check_url_blocks_loopback_host(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "true")
    with pytest.raises(BlockedURLError):
        await check_url("http://localhost/", load_config())


def test_a_failure_is_reported_with_its_own_detail():
    assert safe_detail(ValueError("connection reset")) == "ValueError: connection reset"


def test_a_failure_detail_a_page_can_choose_is_bounded():
    page_chosen = ValueError("x" * 5000)

    detail = safe_detail(page_chosen)

    assert detail == "ValueError: " + "x" * 188
