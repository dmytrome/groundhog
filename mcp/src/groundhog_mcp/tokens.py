import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal, TypedDict

MAX_DEPTH = 3
TTL_S = 30 * 60
_VERSION = 1

secret = secrets.token_bytes(32)

Refusal = Literal["invalid_token", "expired"]


class Grant(TypedDict):
    v: int
    fetch_url: str
    depth: int
    max_depth: int
    issued: float
    issued_at: float
    parent_source_url: str
    parent_passage_id: str
    label: str


class TokenError(Exception):
    def __init__(self, reason: Refusal) -> None:
        super().__init__(reason)
        self.reason: Refusal = reason


def _mac(body: bytes) -> str:
    digest = hmac.new(secret, body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue(
    *,
    fetch_url: str,
    depth: int,
    parent_source_url: str,
    parent_passage_id: str,
    label: str,
) -> str:
    grant: Grant = {
        "v": _VERSION,
        "fetch_url": fetch_url,
        "depth": depth,
        "max_depth": MAX_DEPTH,
        "issued": time.monotonic(),
        "issued_at": time.time(),
        "parent_source_url": parent_source_url,
        "parent_passage_id": parent_passage_id,
        "label": label,
    }
    body = base64.urlsafe_b64encode(json.dumps(grant, separators=(",", ":")).encode())
    return body.decode().rstrip("=") + "." + _mac(body.rstrip(b"="))


def redeem(token: str) -> Grant:
    body, _, mac = token.partition(".")
    if not body or not hmac.compare_digest(_mac(body.encode()).encode(), mac.encode()):
        raise TokenError("invalid_token")
    try:
        grant: Grant = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError as exc:
        raise TokenError("invalid_token") from exc
    if grant.get("v") != _VERSION or not 0 < grant["depth"] <= grant["max_depth"] <= MAX_DEPTH:
        raise TokenError("invalid_token")
    awake, wall = time.monotonic() - grant["issued"], time.time() - grant["issued_at"]
    if not (0 <= awake <= TTL_S and wall <= TTL_S):
        raise TokenError("expired")
    return grant
