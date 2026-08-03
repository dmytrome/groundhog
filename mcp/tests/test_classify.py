import typing

import pytest

from groundhog_mcp import classify

_HTML = "text/html; charset=utf-8"
# Long enough to be content rather than an interstitial, in the plainest possible way.
_ARTICLE = (
    "The board approved a dividend of forty cents per share, the first increase in "
    "three years. Analysts had expected the payout to stay flat. " * 6
)
# What a challenge page actually renders: a line of explanation and nothing else.
_INTERSTITIAL_BODY = "Verifying you are human. This may take a few seconds."


def _response(status: int = 200, mime: str = _HTML, headers: dict | None = None) -> dict:
    """A `Network.responseReceived` payload's `response`, in CDP's own shape."""
    return {"status": status, "mimeType": mime, "headers": headers or {}}


def _classify(response, title="", text="", asset=False):
    return classify.classify(response, asset, title, text)


@pytest.mark.parametrize(
    "response, title, text, expected",
    [
        # A normal page: 2xx, HTML, ordinary content.
        (_response(), "An Article", _ARTICLE, "ok"),
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
        (_response(451), "Unavailable For Legal Reasons", "", "blocked"),
        (_response(400), "Bad Request", "", "blocked"),
        (_response(402), "Payment Required", "", "blocked"),
        (_response(405), "Method Not Allowed", "", "blocked"),
        # Non-text bodies: the PDF/image case that otherwise renders to junk.
        (_response(mime="application/pdf"), "", "", "unsupported_content"),
        (_response(mime="image/png"), "", "", "unsupported_content"),
        (_response(mime="application/octet-stream"), "", "", "unsupported_content"),
        (_response(mime="application/zip"), "", "", "unsupported_content"),
        # Textual types stay ok — a caller can still use them.
        (_response(mime="text/plain"), "", "notes", "ok"),
        (_response(mime="application/json"), "", "{}", "ok"),
        (_response(mime="image/svg+xml"), "", "", "ok"),
        # Types Chrome renders in its plain-text viewer.
        (_response(mime="application/javascript"), "", "export const x = 1;", "ok"),
        (_response(mime="application/x-ndjson"), "", '{"a":1}', "ok"),
        (_response(mime="application/x-yaml"), "", "a: 1", "ok"),
        (_response(mime="application/toml"), "", "a = 1", "ok"),
        # Structured suffixes (RFC 6839) are readable text, not binary.
        (_response(mime="application/problem+json"), "", "{}", "ok"),
        (_response(mime="application/xhtml+xml"), "", "<p/>", "ok"),
        (_response(mime="application/ld+json"), "", "{}", "ok"),
    ],
)
def test_status_and_content_type(response, title, text, expected):
    assert _classify(response, title, text) == expected


@pytest.mark.parametrize(
    "headers",
    [
        {"cf-mitigated": "challenge"},
        {"Cf-Mitigated": "Challenge"},  # header names and values are case-insensitive
        {"x-vercel-mitigated": "challenge"},
        {"x-amzn-waf-action": "challenge"},
        {"x-amzn-waf-action": "captcha"},
        {"x-dd-b": "1"},
        {"x-dd-b": "3"},  # the documented value set (1, 2) is not what is really sent
        {"x-datadome-cid": "AHrl..."},
    ],
)
def test_a_mitigation_header_is_decisive_whatever_the_page_says(headers):
    # The point of tier 1: these need no help from the wording, so they hold for a
    # challenge served in any language — which is what a phrase list can never do.
    assert _classify(_response(200, headers=headers), "", "") == "challenge"
    assert _classify(_response(403, headers=headers), "Zugriff verweigert", "") == "challenge"


@pytest.mark.parametrize(
    "headers",
    [
        # "This site is behind vendor X", true of a large share of the web — including
        # every page it serves normally. A captured LinkedIn 999 block carries the first
        # two while having nothing to do with Cloudflare.
        {"server": "cloudflare"},
        {"cf-ray": "9e24d0f51906038a-MAD"},
        {"cf-cache-status": "DYNAMIC"},
        # Issued when a challenge is *passed*: reading it as a block inverts its meaning.
        {"set-cookie": "cf_clearance=abc123; Path=/"},
        # On every API Gateway response, blocked or not.
        {"x-amzn-requestid": "abc-123"},
    ],
)
def test_vendor_presence_headers_are_not_block_signals(headers):
    assert _classify(_response(200, headers=headers), "An Article", _ARTICLE) == "ok"


@pytest.mark.parametrize(
    "url",
    [
        "https://ex.com/cdn-cgi/challenge-platform/h/b/orchestrate/managed/v1",
        "https://ex.com/cdn-cgi/challenge-platform/h/g/orchestrate/captcha/v1",
        "https://geo.captcha-delivery.com/captcha/?initialCid=x",
        "https://captcha.px-cdn.net/xyz/captcha.js",
        "https://client.px-cdn.net/xyz/main.min.js",
        "https://ex.com/_Incapsula_Resource?SWJIYLWA=719",
    ],
)
def test_a_challenge_asset_is_decisive(url):
    assert classify.is_challenge_asset(url) is True
    assert _classify(_response(200), "", _ARTICLE, asset=True) == "challenge"


@pytest.mark.parametrize(
    "url",
    [
        # Cloudflare serves its passive JS-detection probe from the same path on
        # ordinary, unchallenged pages — only the orchestrator means a challenge.
        "https://ex.com/cdn-cgi/challenge-platform/h/b/scripts/jsd/main.js",
        # Ordinary login and contact forms embed Turnstile and reCAPTCHA.
        "https://challenges.cloudflare.com/turnstile/v0/api.js",
        "https://www.google.com/recaptcha/api.js",
        "https://hcaptcha.com/1/api.js",
        # An article that merely mentions the vendor.
        "https://blog.example.com/posts/how-cloudflare-challenge-platform-works",
        "https://ex.com/app.js",
        None,
        12345,
    ],
)
def test_ordinary_assets_are_not_challenge_assets(url):
    assert classify.is_challenge_asset(url) is False


def test_wording_on_an_empty_page_is_a_challenge():
    # No vendor marker at all — the fallback for a vendor we have no fingerprint for.
    assert _classify(_response(200), "Just a moment...", _INTERSTITIAL_BODY) == "challenge"
    # A site suffix in the title must not matter; the emptiness carries the verdict.
    assert _classify(_response(200), "Just a moment... | example.com", "") == "challenge"
    # Body wording alone, with an unremarkable title.
    assert _classify(_response(200), "example.com", _INTERSTITIAL_BODY) == "challenge"


def test_wording_on_a_page_with_real_content_is_not_a_challenge():
    # The false positive that motivated the redesign. An article whose title contains a
    # challenge phrase has a body; an interstitial does not. That is the discriminator,
    # and unlike a title-length ratio it does not need tuning.
    assert _classify(_response(200), "Just a Moment (2024) — a review", _ARTICLE) == "ok"
    assert _classify(_response(200), "Are you a robot? Inside CAPTCHA", _ARTICLE) == "ok"
    assert _classify(_response(200), "How we verify you are human", _ARTICLE) == "ok"


def test_wording_does_not_override_a_more_specific_status():
    # A 404 or a 5xx already describes the response; `rate_limited` is more specific
    # than `challenge`. Only ok/blocked/unknown are upgraded.
    assert _classify(_response(404), "Just a moment...", "") == "not_found"
    assert _classify(_response(503), "Just a moment...", "") == "server_error"
    assert _classify(_response(429), "Just a moment...", "") == "rate_limited"
    # A 403 interstitial is a challenge, not a bare block — the retry advice differs.
    assert _classify(_response(403), "Just a moment...", "") == "challenge"


def test_an_unobserved_response_is_unknown_not_ok():
    assert _classify(None, "An Article", _ARTICLE) == "unknown"


def test_a_challenge_is_recognized_even_without_a_response():
    assert _classify(None, "Just a moment...", "") == "challenge"
    assert _classify(None, "", _ARTICLE, asset=True) == "challenge"


def test_a_status_that_was_never_really_reported_is_unknown_not_ok():
    # `0` is what a service-worker-synthesized or client-blocked request reports, and a
    # missing/non-numeric status is no status at all. Neither is a verified 200.
    assert _classify({"status": 0, "mimeType": _HTML, "headers": {}}, "", "") == "unknown"
    assert _classify({"mimeType": _HTML, "headers": {}}, "", "") == "unknown"
    assert _classify({"status": "200", "mimeType": _HTML, "headers": {}}, "", "") == "unknown"


def test_inputs_are_hostile_shapes_not_trusted_types():
    # Every field is page/server-influenceable, so a non-dict response, a non-string
    # title, or a non-dict headers value must degrade rather than raise on the boundary.
    assert _classify("not-a-dict", None, 42) == "unknown"
    assert _classify({"status": 200, "mimeType": 12345, "headers": []}, None, 42) == "ok"
    assert _classify({"status": 200, "mimeType": _HTML, "headers": {12: "x"}}, "", "") == "ok"


def test_a_pathological_title_is_bounded_before_it_is_lowercased():
    # `classify` runs on the collector's raw title, before `RenderedPage` caps it, and a
    # page chooses that string. Unbounded, this took seconds and hundreds of MB
    # *synchronously* inside the fetch — stalling every concurrent call, not just this one.
    import time

    started = time.perf_counter()
    assert _classify(_response(), "x" * 2_000_000, "") == "ok"
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"pathological title took {elapsed:.2f}s — the bound is gone"


def test_a_body_challenge_phrase_past_the_scan_window_is_not_matched():
    # The body is scanned by a bounded prefix so a multi-megabyte page is cheap. Such a
    # page is not an interstitial anyway, so the emptiness gate rejects it first.
    text = ("x" * 5000) + "ddos protection by cloudflare"
    assert _classify(_response(), "", text) == "ok"


def test_not_content_covers_every_status_except_ok_and_unknown():
    # The set drives whether `research` ranks a page's body; a status added to the
    # enum and forgotten here would silently start feeding error bodies into passages.
    every = set(typing.get_args(classify.RetrievalStatus))
    assert classify.NOT_CONTENT == every - {"ok", "unknown"}
