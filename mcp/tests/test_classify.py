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
        # Every other 4xx serves an error page too — falling through to `ok` would hand
        # that page back as content and rank its body in `research`.
        (_response(451), "Unavailable For Legal Reasons", "blocked in your region", "blocked"),
        (_response(400), "Bad Request", "", "blocked"),
        (_response(402), "Payment Required", "", "blocked"),
        (_response(405), "Method Not Allowed", "", "blocked"),
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
        # Types Chrome renders in its plain-text viewer: readable, and dropping them
        # would silently cost a source its passages in `research`.
        (_response(mime="application/javascript"), "", "export const x = 1;", "ok"),
        (_response(mime="application/x-ndjson"), "", '{"a":1}', "ok"),
        (_response(mime="application/x-yaml"), "", "a: 1", "ok"),
        (_response(mime="application/toml"), "", "a = 1", "ok"),
        # Structured suffixes (RFC 6839) are readable text, not binary.
        (_response(mime="application/problem+json"), "", "{}", "ok"),
        (_response(mime="application/xhtml+xml"), "", "<p/>", "ok"),
        (_response(mime="application/ld+json"), "", "{}", "ok"),
        (_response(mime="application/vnd.api+json"), "", "{}", "ok"),
        (_response(mime="application/vnd.github+json"), "", "{}", "ok"),
        (_response(mime="application/atom+xml"), "", "<feed/>", "ok"),
        (_response(mime="application/vnd.custom+xml"), "", "<x/>", "ok"),
        # Precision: ordinary prose that merely mentions these words is not a challenge.
        (_response(), "How CAPTCHA works", "An article explaining robot detection.", "ok"),
        # A real article whose title happens to contain a sign phrase. A false positive
        # here costs the source every one of its passages in `research`.
        (
            _response(),
            "Just a Moment (2024) — a review of the year's quietest drama",
            "The film opens on a wide shot of an empty road.",
            "ok",
        ),
        # …but a site suffix must not dilute a real interstitial below the threshold.
        # Measuring the phrase against the whole title made this read as content.
        (_response(), "Just a moment... | example.com", "", "challenge"),
        (_response(), "example.com — Attention Required! | Cloudflare", "", "challenge"),
        # The current Cloudflare wording lives in the body, where an older skin put it
        # in the title. Neither list alone covers it.
        (
            _response(),
            "example.com",
            "Verifying you are human. This may take a few seconds.",
            "challenge",
        ),
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


def test_a_status_that_was_never_really_reported_is_unknown_not_ok():
    # `0` is what a service-worker-synthesized or client-blocked request reports, and a
    # missing/non-numeric status is no status at all. Neither is a verified 200.
    assert classify.classify({"status": 0, "mimeType": _HTML, "headers": {}}, "", "") == "unknown"
    assert classify.classify({"mimeType": _HTML, "headers": {}}, "", "") == "unknown"
    bad_type = {"status": "200", "mimeType": _HTML, "headers": {}}
    assert classify.classify(bad_type, "", "") == "unknown"


def test_inputs_are_hostile_shapes_not_trusted_types():
    # Every field is page/server-influenceable, so a non-dict response, a non-string
    # title, or a non-dict headers value must degrade rather than raise on the boundary.
    assert classify.classify("not-a-dict", None, 42) == "unknown"
    assert classify.classify({"status": 200, "mimeType": 12345, "headers": []}, None, 42) == "ok"


def test_a_pathological_title_is_bounded_before_it_is_lowercased_and_split():
    # `classify` runs on the collector's raw title, before `RenderedPage` caps it, and a
    # page chooses that string. Unbounded, splitting megabytes of separators took seconds
    # and hundreds of MB *synchronously* inside the fetch — stalling every concurrent
    # call, not just this one. Timed, because the verdict alone would not catch a regression.
    import time

    started = time.perf_counter()
    assert classify.classify(_response(), "|" * 2_000_000, "") == "ok"
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"pathological title took {elapsed:.2f}s — the bound is gone"


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
