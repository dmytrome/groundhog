import asyncio
import json
import urllib.request
from email.message import Message
from typing import IO

MAX_BYTES = 5 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def read_json(url: str, timeout: float, max_bytes: int = MAX_BYTES) -> dict:
    with _opener.open(url, timeout=timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response body exceeded {max_bytes} bytes")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("response body is not a JSON object")
    return data


async def read_json_async(url: str, timeout: float) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, lambda: read_json(url, timeout))
