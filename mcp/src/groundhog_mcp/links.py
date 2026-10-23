import re
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from . import retrieval, safety, sanitize

MAX_LABEL_CHARS = 160
_ESCAPED_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_IMAGE_RE = re.compile(r"!\[((?:\\.|[^\]\\])*)\]\([^)]*\)")
_DIGITS_RE = re.compile(r"\d+")
_TRACKING = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "_ga", "_hsenc", "_hsmi"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class Link(NamedTuple):
    url: str
    label: str
    start: int
    end: int


def _code_span_end(text: str, i: int) -> int:
    run = len(text) - len(text[i:].lstrip("`"))
    size = run - i
    stop = text.find("\n\n", i)
    stop = len(text) if stop < 0 else stop
    j = i + size
    while (j := text.find("`" * size, j, stop)) >= 0:
        after = j + size
        if text[j - 1] != "`" and (after == len(text) or text[after] != "`"):
            return after
        j = after
        while j < stop and text[j] == "`":
            j += 1
    return i + size


def _label_end(text: str, i: int) -> int | None:
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if text.startswith("\n\n", j):
            return None
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def _skip_space(text: str, k: int) -> int:
    while k < len(text) and text[k] in " \t\n":
        k += 1
    return k


def _destination(text: str, k: int) -> tuple[str, int] | None:
    k = _skip_space(text, k)
    if k < len(text) and text[k] == "<":
        close = text.find(">", k + 1)
        if close < 0 or "\n" in text[k + 1 : close] or "<" in text[k + 1 : close]:
            return None
        dest, k = text[k + 1 : close], close + 1
    else:
        start, depth = k, 0
        while k < len(text):
            c = text[k]
            if c == "\\":
                k += 2
                continue
            if c in " \t\n":
                break
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    break
                depth -= 1
            k += 1
        dest = text[start:k]
    k = _skip_space(text, k)
    if k < len(text) and text[k] in "\"'(":
        close = text.find(")" if text[k] == "(" else text[k], k + 1)
        if close < 0:
            return None
        k = _skip_space(text, close + 1)
    if k >= len(text) or text[k] != ")":
        return None
    return _ESCAPED_RE.sub(r"\1", dest), k + 1


def _label(raw: str) -> str:
    text = _IMAGE_RE.sub(lambda m: m.group(1), raw)
    return sanitize.clean_field(_ESCAPED_RE.sub(r"\1", text), MAX_LABEL_CHARS) or ""


def find(markdown: str, base_url: str) -> list[Link]:
    raws = retrieval.split_lines(markdown)
    fenced = retrieval.fenced_lines([raw.rstrip("\n") for raw in raws])
    skip: dict[int, int] = {}
    pos = 0
    for index, raw in enumerate(raws):
        if index in fenced:
            skip[pos] = pos + len(raw)
        pos += len(raw)

    found: list[Link] = []
    i = 0
    while i < len(markdown):
        if i in skip:
            i = skip[i]
            continue
        c = markdown[i]
        if c == "\\":
            i += 2
        elif c == "`":
            i = _code_span_end(markdown, i)
        elif c == "[" or markdown.startswith("![", i):
            image = c == "!"
            opening = i + 1 if image else i
            close = _label_end(markdown, opening)
            parsed = (
                _destination(markdown, close + 2)
                if close is not None and markdown.startswith("(", close + 1)
                else None
            )
            if close is None or parsed is None:
                i = opening + 1
                continue
            dest, end = parsed
            if not image and (url := safety.safe_url(urljoin(base_url, dest))):
                found.append(Link(url, _label(markdown[opening + 1 : close]), i, end))
            i = end
        else:
            i += 1
    return found


def identity(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING
    ]
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), ""))


def same_page(url: str, page_url: str) -> bool:
    parts, page = urlsplit(url), urlsplit(page_url)
    if not parts.fragment:
        return False
    if identity(url) == identity(page_url):
        return True
    return parts.hostname == page.hostname and parts.path in ("", "/") and not parts.query


def pattern(url: str) -> str:
    parts = urlsplit(identity(url))
    return _DIGITS_RE.sub("#", f"{parts.netloc}{parts.path}?{parts.query}")
