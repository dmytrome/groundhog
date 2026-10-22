import re
from dataclasses import dataclass

import trafilatura

_CHARS_PER_TOKEN = 4
_TRUNCATION_MARKER = "\n\n[... truncated]"


@dataclass
class ExtractMeta:
    author: str | None
    published: str | None
    canonical: str | None


_BACKTICKS_RE = re.compile(r"`+")
_TARGET = r"\((?:[^()]|\([^()]*\))*\)"
_IMAGE_RE = re.compile(r"!\[(?:[^\[\]]|\[[^\]]*\])*\]" + _TARGET)
_LINK_TARGET_RE = re.compile(r"\]" + _TARGET)


def fenced(text: str) -> str:
    if not text.strip():
        return ""
    longest = max((len(run) for run in _BACKTICKS_RE.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def readable(markdown: str) -> bool:
    words = _LINK_TARGET_RE.sub("]", _IMAGE_RE.sub("", markdown))
    return any(ch.isalnum() for ch in words)


def truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip() + _TRUNCATION_MARKER, True


def to_document(html: str, url: str) -> tuple[str, ExtractMeta]:
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
    ) or ""
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    doc_url = getattr(doc, "url", None)
    canonical = doc_url if doc_url != url else None
    meta = ExtractMeta(
        author=getattr(doc, "author", None),
        published=getattr(doc, "date", None),
        canonical=canonical,
    )
    return markdown, meta


