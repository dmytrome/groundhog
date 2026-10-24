import logging
import os
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from groundhog_mcp.engine import BrowserUnavailableError
from groundhog_mcp.safety import CallerFacingError
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

_hits: dict[str, deque[float]] = {}
_global_hits: deque[float] = deque()


def _drain(hits: deque[float], window_s: int, now: float) -> None:
    while hits and now - hits[0] > window_s:
        hits.popleft()


def _admit(ip: str) -> str | None:
    now = time.monotonic()
    for key in [key for key, hits in _hits.items() if now - hits[-1] > _RATE_WINDOW_S]:
        del _hits[key]
    mine = _hits.get(ip, deque())
    _drain(mine, _RATE_WINDOW_S, now)
    _drain(_global_hits, _GLOBAL_WINDOW_S, now)
    if len(mine) >= _RATE_LIMIT_PER_HOUR:
        return (
            f"Demo limit: {_RATE_LIMIT_PER_HOUR} fetches/hour per visitor. "
            "Run it yourself with no limit: uvx groundhog-mcp"
        )
    if len(_global_hits) >= _GLOBAL_RATE_LIMIT_PER_MINUTE:
        return "Demo is busy right now, try again in a moment."
    mine.append(now)
    _hits[ip] = mine
    _global_hits.append(now)
    return None


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=_MAX_URL_CHARS)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/api/fetch")
async def fetch(req: FetchRequest, request: Request) -> dict:
    ip = request.client.host if request.client else "unknown"
    if refusal := _admit(ip):
        raise HTTPException(429, refusal)
    try:
        return await read_url(req.url)
    except BrowserUnavailableError as exc:
        _log.exception("browser unavailable for %r", req.url)
        raise HTTPException(502, "Could not fetch that URL right now.") from exc
    except CallerFacingError as exc:
        _log.warning("refused url %r: %s", req.url, exc)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        # The visitor gets a generic line; the operator gets the real cause.
        _log.exception("fetch failed for %r", req.url)
        raise HTTPException(502, "Could not fetch that URL right now.") from exc
