import pytest

from groundhog_mcp.config import load_config
from groundhog_mcp.safety import (
    BlockedURLError,
    check_url,
    is_blocked_ip,
    redacted_url,
    safe_detail,
    safe_url,
)


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


def test_an_ipv6_address_keeps_its_brackets_when_redacted():
    assert redacted_url("http://[::1]:9222/?token=secret") == "http://[::1]:9222"


@pytest.mark.parametrize("value", ["user:secret@host:abc", "//user:secret@host:abc"])
def test_misconfigured_url_redaction_drops_credentials(value: str) -> None:
    assert redacted_url(value) == "host:abc"


@pytest.mark.parametrize(
    "value",
    [
        "https://ex.com/a\u200bb",
        "https://ex.com/a\nb",
        "https://ex.com/" + "x" * 3000,
        "javascript:alert(1)",
        "ftp://ex.com/path",
        "https://user:secret@ex.com/",
        "https:///missing-host",
        "http://[::1/path",
    ],
)
def test_unsafe_citations_are_dropped_instead_of_rewritten(value: str) -> None:
    assert safe_url(value) is None


def test_a_safe_citation_preserves_its_exact_url() -> None:
    value = "https://ex.com/a%20b?x=1#part"
    assert safe_url(value) == value


def test_blocked_address_details_are_suppressed() -> None:
    assert safe_detail(BlockedURLError("internal.example -> 10.0.0.1")) == "blocked by SSRF policy"


def test_page_chosen_error_details_are_sanitized() -> None:
    assert safe_detail(ValueError("bad\u200b\ntext")) == "ValueError: bad text"


def test_redaction_keeps_only_scheme_host_and_port() -> None:
    assert redacted_url("https://user:secret@ex.com:8443/path?token=private#fragment") == (
        "https://ex.com:8443"
    )


@pytest.mark.parametrize("value", ["ftp://ex.com/", "https://user:secret@ex.com/", "https:///x"])
async def test_citations_and_fetches_reject_the_same_unsafe_url_rules(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    assert safe_url(value) is None
    with pytest.raises(BlockedURLError):
        await check_url(value, load_config())
