import trafilatura

_CHARS_PER_TOKEN = 4
_TRUNCATION_MARKER = "\n\n[... truncated]"


def truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip() + _TRUNCATION_MARKER, True


def to_markdown(html: str, text_fallback: str, url: str, max_tokens: int) -> tuple[str, bool]:
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
    )
    if not markdown:
        markdown = text_fallback or ""
    return truncate(markdown, max_tokens)
