import asyncio
import ipaddress
from urllib.parse import urlparse

from .config import Config

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedURLError(Exception):
    """Raised when a URL is disallowed by the SSRF guard."""


def is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
        return True
    return isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT


async def check_url(url: str, cfg: Config) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"scheme not allowed: {parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise BlockedURLError("credentials in URL are not allowed")
    host = parsed.hostname
    if not host:
        raise BlockedURLError("URL has no host")
    if not cfg.block_private_ips:
        return
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    for info in infos:
        ip = info[4][0]
        if is_blocked_ip(ip):
            raise BlockedURLError(f"blocked address: {host} -> {ip}")
