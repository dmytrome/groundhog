import pytest

from groundhog_mcp import classify

_HTML = "text/html; charset=utf-8"


def _response(status: int = 200, mime: str = _HTML, headers: dict | None = None) -> dict:
    """A `Network.responseReceived` payload's `response`, in CDP's own shape."""
    return {"status": status, "mimeType": mime, "headers": headers or {}}


@pytest.mark.parametrize(
    "response, title, text, expected",
    [
        # A normal page: 2xx, HTML, ordinary content.
        (_response(), "An Article", "Body about cats and dogs.", "ok"),
        # Status-code mapping.
        (_response(429), "Too Many Requests", "", "rate_limited"),
        (_response(403), "Forbidden", "", "blocked"),
        (_response(401), "Unauthorized", "", "blocked"),
        (_response(404), "Not Found", "", "not_found"),
        (_response(410), "Gone", "", "not_found"),
        (_response(500), "Error", "", "server_error"),
        (_response(503), "Service Unavailable", "", "server_error"),
        # Challenge via a Cloudflare interstitial title, even on a 200.
        (_response(), "Just a moment...", "", "challenge"),
        (_response(), "Attention Required! | Cloudflare", "", "challenge"),
        # Challenge via a body phrase.
        (_response(), "Loading", "Checking your browser before accessing example.com", "challenge"),
        # Challenge via the modern header marker (case-insensitive), whatever the code.
        (_response(403, headers={"cf-mitigated": "challenge"}), "", "", "challenge"),
        (_response(403, headers={"Cf-Mitigated": "challenge"}), "", "", "challenge"),
        # A challenge outranks the status-code mapping: a 403 interstitial is a
        # challenge, not a bare block.
        (_response(403), "Just a moment...", "", "challenge"),
        # Non-text bodies: the PDF/image case that otherwise renders to junk.
        (_response(mime="application/pdf"), "", "", "unsupported_content"),
        (_response(mime="image/png"), "", "", "unsupported_content"),
        (_response(mime="application/octet-stream"), "", "", "unsupported_content"),
        (_response(mime="application/zip"), "", "", "unsupported_content"),
        # Textual types stay ok — a caller can still use them.
        (_response(mime="text/plain"), "", "notes", "ok"),
        (_response(mime="application/json"), "", "{}", "ok"),
        (_response(mime="image/svg+xml"), "", "", "ok"),
        # Precision: ordinary prose that merely mentions these words is not a challenge.
        (_response(), "How CAPTCHA works", "An article explaining robot detection.", "ok"),
    ],
)
def test_classify(response, title, text, expected):
    assert classify.classify(response, title, text) == expected


def test_an_unobserved_response_is_unknown_not_ok():
    # Fail closed: "ok" here would be indistinguishable from a verified 200.
    assert classify.classify(None, "An Article", "Body.") == "unknown"


def test_a_challenge_is_recognized_even_without_a_response():
    # The interstitial is visible in the render alone, so it outranks the missing response.
    assert classify.classify(None, "Just a moment...", "") == "challenge"


def test_inputs_are_hostile_shapes_not_trusted_types():
    # Every field is page/server-influenceable, so a non-dict response, a non-string
    # title, or a numeric status must degrade rather than raise on the boundary.
    assert classify.classify("not-a-dict", None, 42) == "unknown"
    assert classify.classify({"status": "200", "mimeType": 12345, "headers": []}, None, 42) == "ok"


def test_a_body_challenge_phrase_past_the_scan_window_is_not_matched():
    # The body is scanned by a bounded prefix so a multi-megabyte page is cheap; a
    # phrase only appearing far past it is deliberately not caught.
    text = ("x" * 5000) + "ddos protection by cloudflare"
    assert classify.classify(_response(), "", text) == "ok"


def test_not_content_covers_every_status_except_ok_and_unknown():
    # The set drives whether `research` ranks a page's body; a status added to the
    # enum and forgotten here would silently start feeding error bodies into passages.
    import typing

    every = set(typing.get_args(classify.RetrievalStatus))
    assert classify.NOT_CONTENT == every - {"ok", "unknown"}
