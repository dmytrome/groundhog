"""Classify what a fetch actually returned, so a block is never read as content.

The whole product promise is grounding a model can trust; returning a Cloudflare
interstitial, a 403, or a PDF's empty render *as if it were the page* breaks that
silently. This turns the top-level HTTP response, the assets the page requested, and
the rendered title/body into one honest verdict the caller can branch on.

The signals are tiered by precision, and the tiers have hard precedence rather than
being summed into a score. Summing would be dishonest: `cf-mitigated: challenge` is
worth incomparably more than an English phrase in a title, and no weighting expresses
that relationship truthfully.

  1. Vendor mitigation markers — decisive alone. A response header or a challenge
     asset that exists *only* to announce mitigation. Language-independent, so they
     survive both localization and the vendor rewriting its copy.
  2. Status codes — the coarse fallback.
  3. Structural emptiness — an interstitial renders almost no text, whatever it says.
  4. Wording — never decides alone. Only sharpens a page that is already empty.

The tier-1/tier-4 split is the important one. Detecting a challenge by its English
prose fails on the two things that happen constantly: vendors rewrite the wording, and
most of the web is served in other languages. Detecting it by `cf-mitigated` does not.

Deliberately *not* used as block signals: `server: cloudflare`, `cf-ray`, `__cf_bm`,
`_abck`, `_px3`. Those mean "this site sits behind vendor X", which is true of a large
share of the web including every page it serves normally — and a captured LinkedIn 999
block carries `server: cloudflare` and `cf-ray` while having nothing to do with
Cloudflare. `cf_clearance` is excluded for the opposite reason: it is issued when a
challenge is *passed*, so reading it as a block inverts its meaning.

Every input here is attacker-influenceable (a server sets its own status line and
headers; a page writes its own title). The output is this module's own closed enum,
so a hostile page can at worst mislabel *itself* — it cannot smuggle text through.
"""

from typing import Literal

from . import sanitize

RetrievalStatus = Literal[
    "ok",
    "challenge",
    "blocked",
    "rate_limited",
    "not_found",
    "server_error",
    "unsupported_content",
    "unknown",
]

# Statuses whose body is an error or interstitial page rather than the content that
# was asked for. `unknown` is deliberately absent: it means the response was never
# observed, not that the body is known to be junk.
NOT_CONTENT: frozenset[str] = frozenset(
    {"challenge", "blocked", "rate_limited", "not_found", "server_error", "unsupported_content"}
)

# Tier 1 — headers that exist only to announce mitigation, so unlike a CDN's `server`
# header they cannot fire on an ordinary page. Cloudflare documents `cf-mitigated` as
# present on every challenge type, which makes it the single most reliable signal here.
_MITIGATION_HEADER_VALUES: dict[str, tuple[str, ...]] = {
    "cf-mitigated": ("challenge",),
    "x-vercel-mitigated": ("challenge",),
    "x-amzn-waf-action": ("challenge", "captcha"),
}
# DataDome's, where presence is the signal: its documented value set (1, 2) does not
# match what it actually sends (3 observed live), so matching on value would miss blocks.
_MITIGATION_HEADERS_PRESENT = ("x-dd-b", "x-datadome-cid")

# Tier 1 — assets only a challenge loads. Matched against the URLs the page actually
# requested rather than against its markup: the same string inside HTML could be an
# article *about* Cloudflare, and the collector strips `<script>` out of the markup it
# returns anyway. Each entry is a set of substrings that must all appear in one URL.
#
# Cloudflare's needs `/orchestrate/` as well as the challenge-platform path, because
# that path also serves the passive JS-detection probe that Bot Management injects into
# ordinary, unchallenged pages. Turnstile's own widget URL is deliberately absent for
# the same reason: ordinary login and contact forms embed it.
# Lower-cased, because the URL they are matched against is.
_CHALLENGE_ASSETS: tuple[tuple[str, ...], ...] = (
    ("/cdn-cgi/challenge-platform/", "/orchestrate/"),  # Cloudflare
    ("captcha-delivery.com",),  # DataDome
    ("captcha.px-cdn.net",),  # PerimeterX / HUMAN
    ("client.px-cdn.net",),
    ("_incapsula_resource",),  # Imperva
)

# Tier 3 — an interstitial renders almost nothing: a line of explanation and a spinner.
# Real content, in any language, runs to thousands of characters. This is what lets the
# wording below be trusted, and it is why an article whose *title* happens to contain a
# challenge phrase is not mistaken for one: it has a body.
_MAX_INTERSTITIAL_TEXT_CHARS = 600

# A challenge page shows its interstitial at the top; scan a bounded prefix rather
# than lowercasing a multi-megabyte body.
_SCAN_CHARS = 4000

# Tier 4 — wording. Never decides alone, so these can stay broad substrings: the
# structural check above is what supplies the precision. Cloudflare's current skin puts
# the human-check wording in the body where an older one put it in the title, and some
# use both, so those phrases belong to neither list exclusively.
_HUMAN_CHECK_SIGNS = (
    "verify you are human",
    "verifying you are human",
    "are you a robot",
)
_TITLE_SIGNS = _HUMAN_CHECK_SIGNS + (
    "just a moment",
    "attention required",  # "Attention Required! | Cloudflare"
    "checking if the site connection is secure",
)
# Matched against the rendered text, so every phrase here must be one a *reader* sees.
# Cloudflare's "enable javascript and cookies to continue" is deliberately absent: it
# lives in a <noscript>, which `innerText` never returns with scripting on, so including
# it would look like coverage while never once matching.
_BODY_SIGNS = _HUMAN_CHECK_SIGNS + (
    "checking your browser before accessing",
    "ddos protection by cloudflare",
    "performance & security by cloudflare",
    "please stand by, while we are checking your browser",
    "verify you are human by completing the action",
)

# MIME essences that carry text a reader (and the extractor) can work with. Anything
# else present — application/pdf, images, octet-stream, archives — renders to an empty
# or junk body, so it is reported as unsupported rather than returned as if it were text.
# The `+json`/`+xml` families are matched by suffix below, so listing members of them
# here would be dead weight that drifts out of step with that rule. These are the types
# Chrome renders in its plain-text viewer without a suffix to recognize them by.
_TEXTUAL_TYPES = frozenset(
    {
        "application/xml",
        "application/json",
        "application/javascript",
        "application/x-javascript",
        "application/ecmascript",
        "application/ndjson",
        "application/x-ndjson",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/graphql",
    }
)

# The verdicts an interstitial may override. A 404 or a 5xx already describes the
# response accurately, and `rate_limited` is more specific than `challenge`.
_OVERRIDABLE_BY_INTERSTITIAL = ("ok", "blocked", "unknown")


def is_challenge_asset(url: object) -> bool:
    """Whether `url` is an asset only an anti-bot challenge loads.

    Public because the caller sees each request as it happens and tests it there,
    which keeps the marker list here and stops the engine retaining every URL a page
    requested just to search it later.
    """
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return any(all(part in lowered for part in marker) for marker in _CHALLENGE_ASSETS)


def classify(
    response: object, challenge_asset_seen: bool, title: object, text: object
) -> RetrievalStatus:
    """Reduce a fetch's main response and rendered content to one status.

    `response` is CDP's `Network.responseReceived` payload for the document that is
    current at extraction time, or None when none was observed.
    `challenge_asset_seen` says whether the page requested an asset only a challenge
    loads — see `is_challenge_asset`.
    """
    fields = response if isinstance(response, dict) else None
    if challenge_asset_seen or _mitigation_header(fields.get("headers") if fields else None):
        return "challenge"
    if fields is not None and _is_unsupported(fields.get("mimeType")):
        return "unsupported_content"
    # A missing response yields `unknown` from the same mapping — failing closed, since
    # "ok" there would be indistinguishable from a verified 200. It stays overridable
    # below: a page can still be recognizably an interstitial with no response observed.
    status = _from_status(fields.get("status") if fields is not None else None)
    if status in _OVERRIDABLE_BY_INTERSTITIAL and _reads_as_interstitial(title, text):
        return "challenge"
    return status


def _mitigation_header(headers: object) -> bool:
    if not isinstance(headers, dict):
        return False
    for name, value in headers.items():
        if not isinstance(name, str):
            continue
        # Header names are case-insensitive; CDP preserves the server's casing.
        lowered = name.lower()
        if lowered in _MITIGATION_HEADERS_PRESENT:
            return True
        expected = _MITIGATION_HEADER_VALUES.get(lowered)
        if expected and isinstance(value, str) and value.strip().lower() in expected:
            return True
    return False


def _reads_as_interstitial(title: object, text: object) -> bool:
    """Whether the render is both challenge-worded and too empty to be content.

    Both halves are required. The wording alone is what produced false positives —
    an article titled "Just a Moment (2024)" is not a challenge — and a page being
    short alone means nothing. Together they are specific, and the emptiness half is
    what carries the judgement on a page whose language we do not model.
    """
    if not isinstance(text, str) or len(text.strip()) > _MAX_INTERSTITIAL_TEXT_CHARS:
        return False
    # Bounded before it is lowercased: this runs on the collector's *raw* title, before
    # `RenderedPage` caps it, and a page chooses that string. The cap is the one the
    # caller will see anyway, so nothing matchable is lost.
    title_l = title[: sanitize.MAX_TITLE_CHARS].lower() if isinstance(title, str) else ""
    if any(sign in title_l for sign in _TITLE_SIGNS):
        return True
    return any(sign in text[:_SCAN_CHARS].lower() for sign in _BODY_SIGNS)


def _is_unsupported(mime_type: object) -> bool:
    if not isinstance(mime_type, str):
        return False
    essence = mime_type.split(";", 1)[0].strip().lower()
    if not essence or essence.startswith("text/"):
        return False
    # The structured-suffix convention (RFC 6839): `application/problem+json` and
    # `application/vnd.api+json` are as readable as `application/json`, and treating
    # them as binary would drop the source's passages in `research`.
    return essence not in _TEXTUAL_TYPES and not essence.endswith(("+json", "+xml"))


def _from_status(http_status: object) -> RetrievalStatus:
    if not isinstance(http_status, int) or http_status <= 0:
        # Fail closed for the same reason a missing response does. `0` is what a
        # request answered by a service worker, or blocked by the client, reports —
        # not a verified 200.
        return "unknown"
    if http_status == 429:
        return "rate_limited"
    if http_status in (404, 410):
        return "not_found"
    if 500 <= http_status <= 599:
        return "server_error"
    if 400 <= http_status <= 499:
        # 401/403 are the common ones, but 451, 400, 402 and 405 serve error pages
        # too. Falling through to `ok` would hand that page back as content and, in
        # `research`, rank its body against the query. `http_status` carries the code.
        return "blocked"
    return "ok"
