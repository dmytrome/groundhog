from typing import Literal, TypedDict

# Caps for the page-authored strings that reach a tool result. They live here,
# beside `clean_field`, so a new call site picks an existing bound instead of
# inventing one — the drift that let several fields go unbounded.
MAX_TITLE_CHARS = 300
MAX_URL_CHARS = 2048
MAX_HEADING_CHARS = 200
MAX_ERROR_CHARS = 200
MAX_META_CHARS = 300
MAX_LANG_CHARS = 35  # BCP-47 language tags are short; anything longer is not one
MAX_SNIPPET_CHARS = 500
MAX_PUBLISHED_CHARS = 40
MAX_ENGINE_CHARS = 60
# A threat report identifies a payload; it does not reproduce it.
MAX_EXCERPT_CHARS = 80
MAX_LOCATION_CHARS = 120
MAX_SPAN_REASON_CHARS = 80

# Invisible and occupying no width: removing these cannot change how text reads.
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
# Invisible but occupying width — fillers and line/paragraph separators. Deleting
# these would join words a human sees as separate (`hello⠀world` -> `helloworld`),
# so they are replaced by a space rather than dropped.
_SPACE_LIKE = frozenset("\u3164\u2800\u180e\u2028\u2029")
# The variation-selector supplement, immediately above the Tag block: a prominent
# channel for hiding a payload in text a model reads, and absent from ordinary
# prose. The base block U+FE00-FE0F is deliberately NOT included — U+FE0F is the
# emoji presentation selector, so flagging it would fill `threats` with false
# positives and strip the selector out of every emoji in the page.
_VARIATION_SUP_LO, _VARIATION_SUP_HI = 0xE0100, 0xE01EF
# Directional marks, embeddings/overrides, and isolates (U+2066-U+2069) — the
# channels used to reorder or smuggle text past a human reader.
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Unicode Tag block: a full ASCII mirror rendered invisibly — the canonical
# "invisible instructions" smuggling channel for prompt injection.
_TAG_LO, _TAG_HI = 0xE0000, 0xE007F


ThreatType = Literal[
    "hidden_css",
    "hidden_attribute",
    "hidden_template",
    "report_truncated",
    "final_url_suppressed",
    "detection_degraded",
    "strip_incomplete",
    "zero_width",
    "bidi",
    "tag",
]


class Threat(TypedDict):
    type: ThreatType
    reason: str
    location: str | None
    excerpt: str


def _category(ch: str) -> ThreatType | None:
    code = ord(ch)
    if _TAG_LO <= code <= _TAG_HI:
        return "tag"
    if ch in _ZERO_WIDTH or ch in _SPACE_LIKE:
        return "zero_width"
    if _VARIATION_SUP_LO <= code <= _VARIATION_SUP_HI:
        return "zero_width"
    if ch in _BIDI:
        return "bidi"
    return None


def _without_controls(text: str) -> str:
    """Replace C0/C1 control characters with a space.

    The fields this runs on are single-line by nature — a title, a URL, an error
    detail — so a newline in one is a free line break in model context. `urlparse`
    also discards tab/CR/LF silently, which would otherwise let a URL validate as
    one string and be returned as another.

    Replaced rather than deleted, for the same reason as `_SPACE_LIKE`: a newline
    or tab separates words, so dropping it would join two the reader sees apart.
    A URL containing one is rejected outright by `safe_url`, which compares against
    the original, so this never rewrites a link.
    """
    return "".join(" " if ch < " " or "\x7f" <= ch <= "\x9f" else ch for ch in text)


def clean_field(value: object, max_chars: int) -> str | None:
    """Sanitize and cap one page-authored string.

    Every string a page controls reaches the model somewhere in a tool result, so
    each one is stripped of invisible characters and bounded. Routing them all
    through here is what keeps a new field from quietly becoming a smuggling
    channel: the rule lives in one place rather than at each call site.
    """
    # Values arriving from the page's own JS world are a claim, not a guarantee:
    # `document.title` can be shadowed to return a number. Reject here, once.
    if not isinstance(value, str) or not value:
        return None
    return _without_controls(strip_invisible(value)).strip()[:max_chars] or None


def counts(text: str) -> dict[str, tuple[ThreatType, int]]:
    """Invisible characters in `text`: category and occurrences, by character."""
    found: dict[str, tuple[ThreatType, int]] = {}
    for ch in text:
        category = _category(ch)
        if category:
            _, seen = found.get(ch, (category, 0))
            found[ch] = (category, seen + 1)
    return found


def strip_invisible(text: str, found: dict[str, tuple[ThreatType, int]] | None = None) -> str:
    """`text` with every invisible character removed.

    Characters that occupy width become a space, so removing them cannot merge two
    words a human reads as separate; true zero-width characters are dropped. Pass
    `found` when the caller has already scanned the text, to skip a second pass over
    what can be a multi-megabyte document.
    """
    found = counts(text) if found is None else found
    if not found:
        return text
    return text.translate({ord(ch): (" " if ch in _SPACE_LIKE else None) for ch in found})


def notice(threat_type: ThreatType, reason: str) -> Threat:
    """A finding about the report itself rather than about page content.

    Carries no excerpt or location because no element on the page produced it — and
    keeping that shape in one place is what stops a caller inventing a fifth variant.
    """
    return {"type": threat_type, "reason": reason, "location": None, "excerpt": ""}


def threats(found: dict[str, tuple[ThreatType, int]]) -> list[Threat]:
    """Report a scan as threats.

    Counts stay data until here, so no caller has to recover them by parsing the
    text this function formats.
    """
    return [notice(category, f"U+{ord(ch):04X} x{n}") for ch, (category, n) in found.items()]
