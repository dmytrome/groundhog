import base64
import json
import time

import pytest

from groundhog_mcp import tokens


def _issue(depth: int = 1) -> str:
    return tokens.issue(
        fetch_url="https://a.org/Paper?id=7#s2",
        depth=depth,
        parent_source_url="https://b.org/",
        parent_passage_id="p:abc:0-10",
        label="the paper",
    )


def _refusal(token: str) -> str:
    with pytest.raises(tokens.TokenError) as caught:
        tokens.redeem(token)
    return caught.value.reason


def test_a_token_gives_back_exactly_what_it_was_issued_for() -> None:
    grant = tokens.redeem(_issue())

    assert grant["fetch_url"] == "https://a.org/Paper?id=7#s2"
    assert grant["depth"] == 1
    assert grant["max_depth"] == tokens.MAX_DEPTH
    assert grant["parent_passage_id"] == "p:abc:0-10"


def test_a_token_whose_payload_was_edited_is_refused() -> None:
    body, mac = _issue().split(".")
    grant = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    grant["fetch_url"] = "http://169.254.169.254/latest/meta-data/"
    forged = base64.urlsafe_b64encode(json.dumps(grant).encode()).decode().rstrip("=")

    assert _refusal(f"{forged}.{mac}") == "invalid_token"


@pytest.mark.parametrize("token", ["", "x", "x.y", ".", "not base64!.abc", "abc.é", "é.abc"])
def test_garbage_is_refused(token: str) -> None:
    assert _refusal(token) == "invalid_token"


def test_a_token_past_its_lifetime_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _issue()
    now = time.monotonic()
    monkeypatch.setattr(tokens.time, "monotonic", lambda: now + tokens.TTL_S + 1)

    assert _refusal(token) == "expired"


def test_a_token_from_before_a_restart_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _issue()
    monkeypatch.setattr(tokens, "secret", b"another process" * 2)

    assert _refusal(token) == "invalid_token"


def test_a_token_deeper_than_the_limit_is_refused() -> None:
    assert _refusal(_issue(depth=tokens.MAX_DEPTH + 1)) == "invalid_token"
    assert tokens.redeem(_issue(depth=tokens.MAX_DEPTH))["depth"] == tokens.MAX_DEPTH


def test_a_token_kept_while_the_machine_slept_past_its_lifetime_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _issue()
    now = time.time()
    monkeypatch.setattr(tokens.time, "time", lambda: now + tokens.TTL_S + 1)

    assert _refusal(token) == "expired"


def test_a_wall_clock_set_back_does_not_stretch_a_tokens_life(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _issue()
    now, wall = time.monotonic(), time.time()
    monkeypatch.setattr(tokens.time, "monotonic", lambda: now + tokens.TTL_S + 1)
    monkeypatch.setattr(tokens.time, "time", lambda: wall - 3600)

    assert _refusal(token) == "expired"
