import hashlib
from typing import TypedDict

import py3langid

from .extract import ExtractMeta

_AUTHOR_KEYS = ("author", "article:author", "dc.creator")
_PUBLISHED_KEYS = ("article:published_time", "datepublished", "dc.date")
_MODIFIED_KEYS = ("article:modified_time", "datemodified", "og:updated_time")
_MIN_CHARS_FOR_DETECTION = 20
_DETECTION_SAMPLE_CHARS = 2000
# A byline is short; trafilatura's text heuristic otherwise scrapes nav/footer
# blocks (e.g. Wikipedia's authority-control box) as the author. Reject anything
# too long to be one — misattributing an author is worse than reporting none.
_MAX_AUTHOR_CHARS = 80
_MAX_AUTHOR_WORDS = 8


def _plausible_author(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = " ".join(value.split())
    if len(collapsed) > _MAX_AUTHOR_CHARS or len(collapsed.split()) > _MAX_AUTHOR_WORDS:
        return None
    return collapsed


class Provenance(TypedDict):
    content_hash: str
    word_count: int
    author: str | None
    published: str | None
    modified: str | None
    canonical: str | None
    language: str | None


def _first(meta: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = meta.get(key)
        if value:
            return value
    return None


def _detect_language(text: str, hint: str | None) -> str | None:
    sample = text.strip()
    if len(sample) < _MIN_CHARS_FOR_DETECTION:
        return hint or None
    try:
        lang, _ = py3langid.classify(sample[:_DETECTION_SAMPLE_CHARS])
        return lang
    except Exception:
        return hint or None


def build(markdown: str, extract_meta: ExtractMeta, engine_meta: dict) -> Provenance:
    raw = {k.lower(): v for k, v in engine_meta.get("meta", {}).items()}
    return {
        "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "word_count": len(markdown.split()),
        "author": _plausible_author(extract_meta.author) or _first(raw, _AUTHOR_KEYS),
        "published": extract_meta.published or _first(raw, _PUBLISHED_KEYS),
        "modified": _first(raw, _MODIFIED_KEYS),
        "canonical": extract_meta.canonical
        or engine_meta.get("canonical")
        or _first(raw, ("canonical",)),
        "language": _detect_language(markdown, engine_meta.get("lang")),
    }
