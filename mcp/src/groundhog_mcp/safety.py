import asyncio
import ipaddress
from urllib.parse import ParseResult, urlparse

from . import sanitize
from .config import ALLOWED_SCHEMES, Config

_BLOCKED_MESSAGE = "blocked by SSRF policy"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class CallerFacingError(Exception):
    pass


class InvalidArgument(CallerFacingError, ValueError):
    pass


class BlockedURLError(Exception):
    pass


def _url_fault(parsed: ParseResult) -> str | None:
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"scheme not allowed: {parsed.scheme!r}"
    if parsed.username or parsed.password:
        return "credentials in URL are not allowed"
    if not parsed.hostname:
        return "URL has no host"
    return None


def safe_url(url: object) -> str | None:
    cleaned = sanitize.clean_field(url, sanitize.MAX_URL_CHARS)
    if cleaned is None or cleaned != url:
        return None
    try:
        return None if _url_fault(urlparse(cleaned)) else cleaned
    except ValueError:
        return None


def redacted_url(url: str) -> str:
    parts = urlparse(url)
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        host = port = None
    if host and parts.scheme:
        shown = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{shown}{f':{port}' if port else ''}"
    return sanitize.clean_field(url.rsplit("@", 1)[-1], sanitize.MAX_URL_CHARS) or "(unset)"


def safe_detail(exc: BaseException) -> str:
    if isinstance(exc, BlockedURLError):
        return _BLOCKED_MESSAGE
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
