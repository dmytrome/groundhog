import asyncio
import ipaddress
from urllib.parse import ParseResult, urlparse

from . import sanitize
from .config import ALLOWED_SCHEMES, Config

_BLOCKED_MESSAGE = "blocked by SSRF policy"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedURLError(Exception):
    """Raised when a URL is disallowed by the SSRF guard."""


def _url_fault(parsed: ParseResult) -> str | None:
    """Why this URL may not be used, or None. The rules that need no I/O."""
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"scheme not allowed: {parsed.scheme!r}"
    if parsed.username or parsed.password:
        return "credentials in URL are not allowed"
    if not parsed.hostname:
        return "URL has no host"
    return None


def safe_url(url: object) -> str | None:
    """A URL that may be handed to a model as a citation, or nothing.

    Never rewritten: a truncated or de-escaped URL is still a valid-looking URL
    pointing somewhere else, so cleaning either leaves it identical or discards it.
    Shares `_url_fault` with `check_url`, so a rule added once applies to every
    model-facing URL whether it came from a page, a search engine or a redirect.
    """
    cleaned = sanitize.clean_field(url, sanitize.MAX_URL_CHARS)
    if cleaned is None or cleaned != url:
        return None
    return None if _url_fault(urlparse(cleaned)) else cleaned


def redacted_url(url: str) -> str:
    """A URL reduced to scheme, host and port — enough to diagnose, safe to echo.

    `cdp_url` is reported by `status` and quoted in the remediation hint, so it
    lands in model context and transcripts. A hosted browser endpoint commonly
    carries its credential in userinfo or a query parameter; `config` already
    refuses to echo `SEARXNG_URL` for the same reason.
    """
    parts = urlparse(url)
    host = parts.hostname
    try:
        port = parts.port  # lazily parsed, so this is what raises on a bad value
    except ValueError:
        host = port = None
    if host and parts.scheme:
        return f"{parts.scheme}://{host}{f':{port}' if port else ''}"
    # No scheme, or unparseable — `CDP_URL=127.0.0.1:9222` is a common misconfiguration
    # and `urlparse` reads the host as the scheme. Show it, since the operator has to
    # fix it, but drop anything before an `@` so a credential cannot ride along.
    return sanitize.clean_field(url.rsplit("@", 1)[-1], sanitize.MAX_URL_CHARS) or "(unset)"


def safe_detail(exc: BaseException) -> str:
    """The failure text that may be shown to a model.

    A guard message names the host and the address it resolved to, so echoing it
    publishes internal topology into model context. A page can also choose the
    text of an exception it provokes — the evals run in its own world. Neither
    belongs in a tool result verbatim, and every tool needs the same answer.
    """
    if isinstance(exc, BlockedURLError):
        return _BLOCKED_MESSAGE  # the host the caller asked for is already known to them
    if isinstance(exc, TimeoutError):
        return "fetch timed out"
    detail = sanitize.clean_field(f"{type(exc).__name__}: {exc}", sanitize.MAX_ERROR_CHARS)
    return detail or "fetch failed"


def is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True
    return isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT


async def check_url(url: str, cfg: Config) -> None:
    parsed = urlparse(url)
    fault = _url_fault(parsed)
    if fault:
        raise BlockedURLError(fault)
    host = parsed.hostname
    if not cfg.block_private_ips:
        return
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    for info in infos:
        ip = info[4][0]
        if is_blocked_ip(ip):
            raise BlockedURLError(f"blocked address: {host} -> {ip}")
