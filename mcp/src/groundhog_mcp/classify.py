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
    "empty",
    "too_large",
    "unknown",
]

NOT_CONTENT: frozenset[str] = frozenset(
    {
        "challenge",
        "blocked",
        "rate_limited",
        "not_found",
        "server_error",
        "unsupported_content",
        "empty",
        "too_large",
    }
)

# Tier 1 — headers that exist only to announce mitigation, so unlike a CDN's `server`
# header they cannot fire on an ordinary page. Cloudflare documents `cf-mitigated` as
# present on every challenge type, which makes it the single most reliable signal here.
_MITIGATION_HEADER_VALUES: dict[str, tuple[str, ...]] = {
    "cf-mitigated": ("challenge",),
    "x-vercel-mitigated": ("challenge",),
    "x-amzn-waf-action": ("challenge", "captcha"),
}
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
    ("/cdn-cgi/challenge-platform/", "/orchestrate/"),
    ("captcha-delivery.com",),
    ("captcha.px-cdn.net",),
    ("client.px-cdn.net",),
    ("_incapsula_resource",),
)

_MAX_INTERSTITIAL_TEXT_CHARS = 600

_SCAN_CHARS = 4000

_HUMAN_CHECK_SIGNS = (
    "verify you are human",
    "verifying you are human",
    "are you a robot",
)
_TITLE_SIGNS = _HUMAN_CHECK_SIGNS + (
    "just a moment",
    "attention required",
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

_OVERRIDABLE_BY_INTERSTITIAL = ("ok", "blocked", "unknown")


def is_challenge_asset(url: object) -> bool:
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return any(all(part in lowered for part in marker) for marker in _CHALLENGE_ASSETS)


def classify(
    response: object, challenge_asset_seen: bool, title: object, text: object
) -> RetrievalStatus:
    fields = response if isinstance(response, dict) else None
    if challenge_asset_seen or _mitigation_header(fields.get("headers") if fields else None):
        return "challenge"
    if fields is not None and _is_unsupported(fields.get("mimeType")):
        return "unsupported_content"
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
        lowered = name.lower()
        if lowered in _MITIGATION_HEADERS_PRESENT:
            return True
        expected = _MITIGATION_HEADER_VALUES.get(lowered)
        if expected and isinstance(value, str) and value.strip().lower() in expected:
            return True
    return False


def _reads_as_interstitial(title: object, text: object) -> bool:
    if not isinstance(text, str) or len(text.strip()) > _MAX_INTERSTITIAL_TEXT_CHARS:
        return False
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
    return essence not in _TEXTUAL_TYPES and not essence.endswith(("+json", "+xml"))


def _from_status(http_status: object) -> RetrievalStatus:
    if not isinstance(http_status, int) or http_status <= 0:
        return "unknown"
    if http_status == 429:
        return "rate_limited"
    if http_status in (404, 410):
        return "not_found"
    if 500 <= http_status <= 599:
        return "server_error"
    if 400 <= http_status <= 499:
        return "blocked"
    return "ok"
