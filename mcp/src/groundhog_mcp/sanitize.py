from typing import TypedDict

_ZERO_WIDTH = "​‌‍⁠﻿"
_BIDI = "‪‫‬‭‮"
_INVISIBLE = _ZERO_WIDTH + _BIDI


class Threat(TypedDict):
    type: str
    reason: str
    location: str | None
    excerpt: str


def strip_invisible(text: str, *, strip: bool = True) -> tuple[str, list[Threat]]:
    counts: dict[str, int] = {}
    for ch in text:
        if ch in _INVISIBLE:
            counts[ch] = counts.get(ch, 0) + 1
    if not counts:
        return text, []
    threats: list[Threat] = [
        {
            "type": "zero_width" if ch in _ZERO_WIDTH else "bidi",
            "reason": f"U+{ord(ch):04X} x{n}",
            "location": None,
            "excerpt": "",
        }
        for ch, n in counts.items()
    ]
    out = text.translate({ord(ch): None for ch in counts}) if strip else text
    return out, threats
