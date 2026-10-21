from typing import Literal, NotRequired, TypedDict

MAX_TITLE_CHARS = 300
MAX_URL_CHARS = 2048
MAX_HEADING_CHARS = 200
MAX_ERROR_CHARS = 200
MAX_META_CHARS = 300
MAX_LANG_CHARS = 35  # BCP-47 language tags are short; anything longer is not one
MAX_SNIPPET_CHARS = 500
MAX_PUBLISHED_CHARS = 40
MAX_ENGINE_CHARS = 60
MAX_EXCERPT_CHARS = 80
MAX_LOCATION_CHARS = 120
MAX_SPAN_REASON_CHARS = 80

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
_SPACE_LIKE = frozenset("\u3164\u2800\u180e\u2028\u2029")
_VARIATION_SUP_LO, _VARIATION_SUP_HI = 0xE0100, 0xE01EF
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
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
    seen: NotRequired[int]


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
    return "".join(" " if ch < " " or "\x7f" <= ch <= "\x9f" else ch for ch in text)


def clean_field(value: object, max_chars: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _without_controls(strip_invisible(value)).strip()[:max_chars] or None


def counts(text: str) -> dict[str, tuple[ThreatType, int]]:
    found: dict[str, tuple[ThreatType, int]] = {}
    for ch in text:
        category = _category(ch)
        if category:
            _, seen = found.get(ch, (category, 0))
            found[ch] = (category, seen + 1)
    return found


def strip_invisible(text: str, found: dict[str, tuple[ThreatType, int]] | None = None) -> str:
    found = counts(text) if found is None else found
    if not found:
        return text
    return text.translate({ord(ch): (" " if ch in _SPACE_LIKE else None) for ch in found})


def notice(threat_type: ThreatType, reason: str) -> Threat:
    return {"type": threat_type, "reason": reason, "location": None, "excerpt": ""}


def threats(found: dict[str, tuple[ThreatType, int]]) -> list[Threat]:
    return [notice(category, f"U+{ord(ch):04X} x{n}") for ch, (category, n) in found.items()]
