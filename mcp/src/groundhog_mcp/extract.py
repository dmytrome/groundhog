import re
from dataclasses import dataclass

import trafilatura

_CHARS_PER_TOKEN = 4
TRUNCATION_MARKER = "\n\n[... truncated]"


@dataclass
class ExtractMeta:
    author: str | None
    published: str | None
    canonical: str | None


_BACKTICKS_RE = re.compile(r"`+")


def fenced(text: str) -> str:
    if not text.strip():
        return ""
    longest = max((len(run) for run in _BACKTICKS_RE.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def cut(text: str, max_tokens: int) -> str:
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    end = text.rfind("\n\n", 0, limit)
    return text[: end if end > 0 else limit].rstrip()


def truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    kept = cut(text, max_tokens)
    if kept == text:
        return text, False
    return kept + TRUNCATION_MARKER, True


def to_document(html: str, url: str) -> tuple[str, ExtractMeta]:
    markdown = (
        trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        )
        or ""
    )
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    doc_url = getattr(doc, "url", None)
    canonical = doc_url if doc_url != url else None
    meta = ExtractMeta(
        author=getattr(doc, "author", None),
        published=getattr(doc, "date", None),
        canonical=canonical,
    )
    return markdown, meta
