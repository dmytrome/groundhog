from dataclasses import dataclass

import trafilatura

_CHARS_PER_TOKEN = 4
_TRUNCATION_MARKER = "\n\n[... truncated]"


@dataclass
class ExtractMeta:
    author: str | None
    published: str | None
    canonical: str | None


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
    # trafilatura echoes the input url back as doc.url when no canonical link exists,
    # so treat a match as "no canonical".
    doc_url = getattr(doc, "url", None)
    canonical = doc_url if doc_url != url else None
    meta = ExtractMeta(
        author=getattr(doc, "author", None),
        published=getattr(doc, "date", None),
        canonical=canonical,
    )
    return markdown, meta


def to_markdown(html: str, text_fallback: str, url: str, max_tokens: int) -> tuple[str, bool]:
    markdown, _ = to_document(html, url)
    if not markdown:
        markdown = text_fallback or ""
    return truncate(markdown, max_tokens)
