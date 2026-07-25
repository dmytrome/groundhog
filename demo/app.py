import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from groundhog_mcp.safety import BlockedURLError
from groundhog_mcp.tools.read_url import read_url

_RATE_LIMIT_PER_HOUR = int(os.environ.get("DEMO_RATE_LIMIT_PER_HOUR", "8"))
_RATE_WINDOW_S = 3600
_MAX_URL_CHARS = 2000

# Caps total demo throughput regardless of source IP, so requests spread across
# many IPs can't collectively saturate the single browser's page concurrency.
_GLOBAL_RATE_LIMIT_PER_MINUTE = int(os.environ.get("DEMO_GLOBAL_RATE_LIMIT_PER_MINUTE", "20"))
_GLOBAL_WINDOW_S = 60

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Groundhog demo")
_log = logging.getLogger("uvicorn.error")

_hits: dict[str, deque[float]] = defaultdict(deque)
_global_hits: deque[float] = deque()


def _window_limited(hits: deque[float], window_s: int, limit: int) -> bool:
    now = time.monotonic()
    while hits and now - hits[0] > window_s:
        hits.popleft()
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


def _rate_limited(ip: str) -> bool:
    limited = _window_limited(_hits[ip], _RATE_WINDOW_S, _RATE_LIMIT_PER_HOUR)
    if not _hits[ip]:  # drop drained buckets so unique IPs can't grow the map unbounded
        del _hits[ip]
    return limited


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=_MAX_URL_CHARS)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/api/fetch")
async def fetch(req: FetchRequest, request: Request) -> dict:
    if _window_limited(_global_hits, _GLOBAL_WINDOW_S, _GLOBAL_RATE_LIMIT_PER_MINUTE):
        raise HTTPException(429, "Demo is busy right now, try again in a moment.")
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(
            429,
            f"Demo limit: {_RATE_LIMIT_PER_HOUR} fetches/hour per visitor. "
            "Run it yourself with no limit: uvx groundhog-mcp",
        )
    try:
        return await read_url(req.url)
    except BlockedURLError as exc:
        # Don't echo the guard's detail — it resolves internal compose hostnames
        # (e.g. `chrome` -> the CDP container's IP) to anonymous visitors.
        _log.warning("blocked url %r: %s", req.url, exc)
        raise HTTPException(400, "That URL is not allowed.") from exc
    except Exception as exc:
        # The visitor gets a generic line; the operator gets the real cause.
        _log.exception("fetch failed for %r", req.url)
        raise HTTPException(502, "Could not fetch that URL right now.") from exc
