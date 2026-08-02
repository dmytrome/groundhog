"""Classify what a fetch actually returned, so a block is never read as content.

The whole product promise is grounding a model can trust; returning a Cloudflare
interstitial, a 403, or a PDF's empty render *as if it were the page* breaks that
silently. This turns the top-level HTTP response — status, MIME type, headers — plus
the rendered title/body into one honest verdict the caller can branch on.

Every input here is attacker-influenceable (a server sets its own status line and
headers; a page writes its own title). The output is this module's own closed enum,
so a hostile page can at worst mislabel *itself* — it cannot smuggle text through.
"""

from typing import Literal

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

# A challenge page shows its interstitial at the top; scan a bounded prefix rather
# than lowercasing a multi-megabyte body.
_SCAN_CHARS = 4000

# The modern Cloudflare marker for a challenged/blocked response. Distinctive enough
# to trust on its own; `server: cloudflare` is not — it fronts countless normal sites.
_CHALLENGE_HEADER = "cf-mitigated"

# Curated to be distinctive to anti-bot interstitials, so ordinary prose that happens
# to mention these words does not trip the verdict. Title matches are the strongest:
# an interstitial's <title> is purpose-built and short.
_TITLE_SIGNS = (
    "just a moment",
    "attention required",  # "Attention Required! | Cloudflare"
    "verify you are human",
    "verifying you are human",
    "are you a robot",
    "checking if the site connection is secure",
)
_BODY_SIGNS = (
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "ddos protection by cloudflare",
    "performance & security by cloudflare",
    "please stand by, while we are checking your browser",
    "verify you are human by completing the action",
)

# MIME essences that carry text a reader (and the extractor) can work with. Anything
# else present — application/pdf, images, octet-stream, archives — renders to an empty
# or junk body, so it is reported as unsupported rather than returned as if it were text.
_TEXTUAL_TYPES = frozenset(
    {
        "application/xhtml+xml",
        "application/xml",
        "application/json",
        "application/ld+json",
        "application/rss+xml",
        "application/atom+xml",
        "image/svg+xml",
    }
)


def classify(response: object, title: object, text: object) -> RetrievalStatus:
    """Reduce a fetch's main response and rendered content to one status.

    `response` is CDP's `Network.responseReceived` payload for the document that is
    current at extraction time, or None when none was observed.

    Order matters: an interstitial (which can arrive on any status code, and is
    recognizable from the render alone) is caught before anything that needs the
    response; a non-text body is caught before the code mapping.
    """
    fields = response if isinstance(response, dict) else None
    if _is_challenge(fields.get("headers") if fields else None, title, text):
        return "challenge"
    if fields is None:
        # Fail closed. Returning "ok" here would be indistinguishable from a verified
        # 200, which is the reassuring answer rather than the true one.
        return "unknown"
    if _is_unsupported(fields.get("mimeType")):
        return "unsupported_content"
    return _from_status(fields.get("status"))


def _is_challenge(headers: object, title: object, text: object) -> bool:
    if isinstance(headers, dict):
        # Header names are case-insensitive; CDP preserves the server's casing.
        for name in headers:
            if isinstance(name, str) and name.lower() == _CHALLENGE_HEADER:
                return True
    title_l = title.lower() if isinstance(title, str) else ""
    if any(sign in title_l for sign in _TITLE_SIGNS):
        return True
    text_l = text[:_SCAN_CHARS].lower() if isinstance(text, str) else ""
    return any(sign in text_l for sign in _BODY_SIGNS)


def _is_unsupported(mime_type: object) -> bool:
    if not isinstance(mime_type, str):
        return False
    essence = mime_type.split(";", 1)[0].strip().lower()
    if not essence or essence.startswith("text/"):
        return False
    return essence not in _TEXTUAL_TYPES


def _from_status(http_status: object) -> RetrievalStatus:
    if not isinstance(http_status, int):
        return "ok"
    if http_status == 429:
        return "rate_limited"
    if http_status in (401, 403):
        return "blocked"
    if http_status in (404, 410):
        return "not_found"
    if 500 <= http_status <= 599:
        return "server_error"
    return "ok"
