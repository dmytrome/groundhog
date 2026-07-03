from typing import TypedDict

# Zero-width, word-joiner, BOM, and soft hyphen — invisible but carry no glyph.
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
# Directional marks, embeddings/overrides, and isolates (U+2066-U+2069) — the
# channels used to reorder or smuggle text past a human reader.
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Unicode Tag block: a full ASCII mirror rendered invisibly — the canonical
# "invisible instructions" smuggling channel for prompt injection.
_TAG_LO, _TAG_HI = 0xE0000, 0xE007F


class Threat(TypedDict):
    type: str
    reason: str
    location: str | None
    excerpt: str


def _category(ch: str) -> str | None:
    if _TAG_LO <= ord(ch) <= _TAG_HI:
        return "tag"
    if ch in _ZERO_WIDTH:
        return "zero_width"
    if ch in _BIDI:
        return "bidi"
    return None


def strip_invisible(text: str, *, strip: bool = True) -> tuple[str, list[Threat]]:
    counts: dict[str, int] = {}
    category: dict[str, str] = {}
    for ch in text:
        cat = _category(ch)
        if cat:
            counts[ch] = counts.get(ch, 0) + 1
            category[ch] = cat
    if not counts:
        return text, []
    threats: list[Threat] = [
        {
            "type": category[ch],
            "reason": f"U+{ord(ch):04X} x{n}",
            "location": None,
            "excerpt": "",
        }
        for ch, n in counts.items()
    ]
    out = text.translate({ord(ch): None for ch in counts}) if strip else text
    return out, threats
