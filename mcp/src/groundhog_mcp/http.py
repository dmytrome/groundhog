import asyncio
import json
import urllib.request

# Big enough for any CDP or search envelope; small enough that a hostile or
# misbehaving endpoint cannot stream us out of memory.
MAX_BYTES = 5 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def read_json(url: str, timeout: float, max_bytes: int = MAX_BYTES) -> dict:
    """GET `url` and parse JSON, without following redirects.

    Redirects are refused rather than followed: the destination of a redirect is
    not operator-controlled even when the configured endpoint is.
    """
    with _opener.open(url, timeout=timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response body exceeded {max_bytes} bytes")
    return json.loads(body)


async def read_json_async(url: str, timeout: float) -> dict:
    """`read_json` off the event loop — urllib blocks."""
    return await asyncio.get_running_loop().run_in_executor(None, lambda: read_json(url, timeout))
