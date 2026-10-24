import bisect
import re
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from markdown_it import MarkdownIt
from markdown_it.common.html_re import HTML_TAG_RE
from markdown_it.common.utils import normalizeReference

from . import safety, sanitize

MAX_LABEL_CHARS = 160
_MAX_DEST_CHARS = sanitize.MAX_URL_CHARS + 64
_MAX_TITLE_CHARS = 2000
_WINDOW_CHARS = 4096
_SAME_LENGTH_NEWLINES = str.maketrans({"\r": "\n", "\0": "\ufffd"})
_ESCAPED_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_IMAGE_RE = re.compile(r"!\[((?:\\.|[^\]\\])*)\]\([^)]*\)")
_DIGITS_RE = re.compile(r"\d+")
_BACKTICKS_RE = re.compile(r"`+")
_HTML_RE = re.compile(HTML_TAG_RE.pattern.removeprefix("^"), HTML_TAG_RE.flags)
_AUTOLINK_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")
_REFERENCE_RE = re.compile(r"\[((?:\\.|[^\[\]\\]){0,999}+)\]")
_PLAIN = r"[^\s()\\]|\\."
_NESTED = _PLAIN
for _ in range(3):
    _NESTED = rf"{_PLAIN}|\((?:{_NESTED}){{0,{_MAX_DEST_CHARS}}}+\)"
_DESTINATION_RE = re.compile(
    rf"[ \t]*+\n?[ \t]*+(?:<([^<>\n]{{0,{_MAX_DEST_CHARS}}}+)>"
    rf"|(?!<)((?:{_NESTED}){{0,{_MAX_DEST_CHARS}}}+))"
    rf"""(?:[ \t]*+\n?[ \t]*+(?:"(?:\\.|[^"\\]){{0,{_MAX_TITLE_CHARS}}}+\""""
    rf"""|'(?:\\.|[^'\\]){{0,{_MAX_TITLE_CHARS}}}+'"""
    rf"|\((?:\\.|[^()\\]){{0,{_MAX_TITLE_CHARS}}}+\)))?+"
    r"[ \t]*+\n?[ \t]*+\)"
)
_BLOCKS = MarkdownIt("commonmark").disable(["inline", "text_join"], ignoreInvalid=True)
_TRACKING = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "_ga", "_hsenc", "_hsmi"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class Link(NamedTuple):
    url: str
    label: str
    start: int
    end: int


class _Opener(NamedTuple):
    at: int
    image: bool


def _label(raw: str) -> str:
    text = _IMAGE_RE.sub(lambda m: m.group(1), raw)
    label = " ".join(_ESCAPED_RE.sub(r"\1", text).split())
    return sanitize.clean_field(label, MAX_LABEL_CHARS) or ""


def _valid(url: str) -> bool:
    try:
        port = urlsplit(url).port
    except ValueError:
        return False
    return port is None or port > 0


def _blocks(text: str) -> tuple[list[tuple[int, int]], dict[str, str]]:
    env: dict[str, dict[str, dict[str, str]]] = {}
    tokens = _BLOCKS.parse(text, env)
    starts = [0]
    for line in text.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    paragraphs = [
        (starts[token.map[0]], min(starts[token.map[1]], len(text)))
        for token in tokens
        if token.type == "inline" and token.map
    ]
    references = {
        key: str(found.get("href", "")) for key, found in env.get("references", {}).items()
    }
    return paragraphs, references


def _escaped(text: str, at: int, start: int) -> bool:
    slashes = 0
    while at - slashes - 1 >= start and text[at - slashes - 1] == "\\":
        slashes += 1
    return slashes % 2 == 1


def _code_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    runs = [(m.start(), m.end()) for m in _BACKTICKS_RE.finditer(text, start, end)]
    by_length: dict[int, list[int]] = {}
    for index, (a, b) in enumerate(runs):
        by_length.setdefault(b - a, []).append(index)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(runs):
        a, b = runs[index]
        if _escaped(text, a, start):
            a += 1
        same = by_length.get(b - a, [])
        later = bisect.bisect_right(same, index)
        if a == b or later == len(same):
            index += 1
            continue
        closer = same[later]
        spans.append((a, runs[closer][1]))
        index = closer + 1
    return spans


def _destination(text: str, at: int, end: int) -> tuple[str, int] | None:
    matched = _DESTINATION_RE.match(text, at, end)
    if not matched:
        return None
    raw = matched.group(1) if matched.group(1) is not None else matched.group(2)
    return _ESCAPED_RE.sub(r"\1", raw), matched.end()


def _reference(
    text: str, opener: int, close: int, end: int, references: dict[str, str]
) -> tuple[str, int] | None:
    after = _REFERENCE_RE.match(text, close + 1, end)
    if after is None and text.startswith("[", close + 1):
        return None
    label = after.group(1) if after and after.group(1) else text[opener + 1 : close]
    key = normalizeReference(label)
    if key not in references:
        return None
    return references[key], after.end() if after else close + 1


def _scan(text: str, start: int, end: int, base_url: str, references: dict[str, str]) -> list[Link]:
    autolinks = {m.start(): m for m in _AUTOLINK_RE.finditer(text, start, end)}
    jumps = dict(_code_spans(text, start, end))
    jumps.update((m.start(), m.end()) for m in _HTML_RE.finditer(text, start, end))
    jumps.update((at, m.end()) for at, m in autolinks.items())
    found: list[Link] = []
    openers: list[_Opener] = []
    inactive: set[int] = set()
    i = start
    while i < end:
        if i in jumps:
            if i in autolinks:
                url = safety.safe_url(autolinks[i].group(1))
                if url and _valid(url):
                    found.append(Link(url, _label(url), i, jumps[i]))
            i = jumps[i]
            continue
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "[":
            openers.append(_Opener(i, False))
            i += 1
        elif c == "!" and text.startswith("[", i + 1):
            openers.append(_Opener(i + 1, True))
            i += 2
        elif c == "]" and openers:
            opener = openers.pop()
            target = None
            if opener.at not in inactive:
                if text.startswith("(", i + 1):
                    target = _destination(text, i + 2, end)
                if target is None and references:
                    target = _reference(text, opener.at, i, end, references)
            if target is None:
                i += 1
                continue
            dest, close = target
            if not opener.image:
                url = safety.safe_url(urljoin(base_url, dest)) if dest else None
                if url and _valid(url):
                    found.append(Link(url, _label(text[opener.at + 1 : i]), opener.at, close))
                inactive.update(o.at for o in openers if not o.image)
            i = close
        else:
            i += 1
    return found


def find(markdown: str, base_url: str, within: list[tuple[int, int]] | None = None) -> list[Link]:
    text = markdown.replace("\r\n", " \n").translate(_SAME_LENGTH_NEWLINES)
    paragraphs, references = _blocks(text)
    found: list[Link] = []
    for start, end in paragraphs:
        if within is None:
            found.extend(_scan(text, start, end, base_url, references))
            continue
        windows: list[list[int]] = []
        for a, b in sorted(within):
            if a < end and start < b:
                low, high = max(start, a - _WINDOW_CHARS), min(end, b + _WINDOW_CHARS)
                if windows and low <= windows[-1][1]:
                    windows[-1][1] = max(windows[-1][1], high)
                else:
                    windows.append([low, high])
        for low, high in windows:
            found.extend(_scan(text, low, high, base_url, references))
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
        netloc = parts.netloc.lower()
    else:
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
    return parts.hostname == page.hostname and parts.path == "" and not parts.query


def pattern(url: str) -> str:
    parts = urlsplit(identity(url))
    return _DIGITS_RE.sub("#", f"{parts.netloc}{parts.path}?{parts.query}")
