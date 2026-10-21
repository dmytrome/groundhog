import math
import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple, TypedDict

from . import sanitize

_CHARS_PER_TOKEN = 4
_UNSPACED = (
    "\u0e00-\u0eff"
    "\u1000-\u109f"
    "\u1100-\u11ff"
    "\u1780-\u17ff"
    "\u3005-\u3007"
    "\u3040-\u30ff"
    "\u3130-\u318f"
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uac00-\ud7af"
    "\uf900-\ufaff"
    "\U00020000-\U0003ffff"
)
MARK_PLANES = (range(0x20000), range(0xE0000, 0xE1000))


def _marks() -> str:
    spans: list[list[int]] = []
    for code in (code for plane in MARK_PLANES for code in plane):
        if unicodedata.category(chr(code)).startswith("M"):
            if spans and spans[-1][1] == code - 1:
                spans[-1][1] = code
            else:
                spans.append([code, code])
    return "".join(f"{chr(first)}-{chr(last)}" for first, last in spans)


_MARKS = _marks()
_UNSPACED_LETTER = rf"(?=[^\W\d_]|[{_MARKS}])[{_UNSPACED}]"
_WORD = rf"[^\W_{_UNSPACED}]+(?:[{_MARKS}]+[^\W_{_UNSPACED}]*)*"
_RUN_RE = re.compile(rf"{_WORD}|(?:{_UNSPACED_LETTER})+|\d+")
_SELECTORS_RE = re.compile("[\u180b-\u180d\u180f\ufe00-\ufe0f\U000e0100-\U000e01ef]")
_UNSPACED_RE = re.compile(_UNSPACED_LETTER)
_HEADING_RE = re.compile(r"^#{1,6}\s+")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
SCORE_DIGITS = 4  # one reported precision for BM25 scores, wherever they surface
_RELEVANCE_FLOOR = 0.5
_MAX_CHUNK_CHARS = 2400
_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+\Z")
_SENTENCE_END_RE = re.compile(r"[.?!] |。")
_K1 = 1.5
_B = 0.75


class Match(TypedDict):
    heading: str | None
    offset: int
    score: float


@dataclass
class Chunk:
    heading: str | None
    offset: int
    text: str
    # Optional because single-document ranking has no URL to attribute to;
    # `research` sets it so pooled passages stay attributable to their source.
    source: str | None = None
    block: int = 0
    fence: str | None = None


class Scored(NamedTuple):
    chunk: Chunk
    score: float


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    normalized = _SELECTORS_RE.sub("", unicodedata.normalize("NFKC", text))
    for run in _RUN_RE.findall(normalized.lower()):
        if _UNSPACED_RE.match(run):
            tokens += run
            tokens += (run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


def _fenced_lines(lines: list[str]) -> set[int]:
    closers: list[tuple[str, int]] = []
    for line in lines:
        closing = _FENCE_CLOSE_RE.match(line)
        run = closing.group(1) if closing else ""
        closers.append((run[:1], len(run)))
    ahead = {"`": [0] * (len(lines) + 1), "~": [0] * (len(lines) + 1)}
    for index in range(len(lines) - 1, -1, -1):
        char, size = closers[index]
        for key, longest in ahead.items():
            longest[index] = max(longest[index + 1], size) if key == char else longest[index + 1]

    inside: set[int] = set()
    index = 0
    while index < len(lines):
        opening = _FENCE_OPEN_RE.match(lines[index])
        if not opening:
            index += 1
            continue
        run, info = opening.group(1), opening.group(2)
        if (run[0] == "`" and "`" in info) or ahead[run[0]][index + 1] < len(run):
            index += 1
            continue
        closed_at = next(
            i
            for i in range(index + 1, len(lines))
            if closers[i][0] == run[0] and closers[i][1] >= len(run)
        )
        inside.update(range(index, closed_at + 1))
        index = closed_at + 1
    return inside


def _lines(markdown: str) -> list[str]:
    return _LINE_RE.findall(markdown)


def _pieces(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    while len(text) - start > _MAX_CHUNK_CHARS:
        end = start + _MAX_CHUNK_CHARS
        cut = text.rfind("\n", start, end) + 1
        if cut <= start:
            ends = [m.end() for m in _SENTENCE_END_RE.finditer(text, start, end)]
            cut = ends[-1] if ends and ends[-1] > start else end
        pieces.append(text[start:cut])
        start = cut
    pieces.append(text[start:])
    return pieces


def chunk_document(markdown: str, source: str | None = None) -> list[Chunk]:
    # Scan line by line so a heading with no blank line before its body still
    # splits into a heading + a searchable body chunk (a blank-line-delimited
    # block would swallow the body into the heading and drop it).
    raws = _lines(markdown)
    fenced = _fenced_lines([raw.rstrip("\n") for raw in raws])
    chunks: list[Chunk] = []
    heading: str | None = None
    lines: list[str] = []
    first = 0
    offset = 0
    pos = 0
    block = 0

    def flush() -> None:
        nonlocal lines, block
        if not lines:
            return
        text = "\n".join(lines)
        pieces = _pieces(text)
        whole_fence = (
            len(pieces) > 1
            and {first, first + len(lines) - 1} <= fenced
            and _FENCE_OPEN_RE.match(lines[0]) is not None
            and _FENCE_CLOSE_RE.match(lines[-1]) is not None
        )
        start = offset
        for piece in pieces:
            chunks.append(
                Chunk(
                    heading=heading,
                    offset=start,
                    text=piece,
                    source=source,
                    block=block,
                    fence=lines[0] if whole_fence else None,
                )
            )
            start += len(piece)
        block += 1
        lines = []

    for index, raw in enumerate(raws):
        line = raw.rstrip("\n")
        start = pos
        pos += len(raw)
        if index in fenced:
            if not lines:
                offset, first = start, index
            lines.append(line)
        elif not line.strip():
            flush()
        elif _HEADING_RE.match(line):
            flush()
            # Repeated once per match/passage and never passed through the token
            # budget, so it is bounded here. Only bounded: the text was already
            # sanitized upstream, and re-stripping would quietly undo the caller's
            # `include_hidden=True`.
            heading = _HEADING_RE.sub("", line).strip()[: sanitize.MAX_HEADING_CHARS]
        else:
            if not lines:
                offset, first = start, index
            lines.append(line)
    flush()
    return chunks


def _bm25(chunks: list[Chunk], query_terms: list[str]) -> list[float]:
    docs = [_tokenize(c.text) for c in chunks]
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    scores: list[float] = []
    for d in docs:
        dl = len(d)
        tf: dict[str, int] = {}
        for term in d:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            if term not in df:
                continue
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / avgdl))
        scores.append(score)
    return scores


def rank(
    chunks: list[Chunk], query: str, max_tokens: int, *, floor: float = 0.0
) -> tuple[list[Scored], bool, int]:
    """Score passages against `query` and admit the best that fit the budget."""
    terms = _tokenize(query)
    scores = _bm25(chunks, terms)
    by_relevance = sorted(
        (i for i, s in enumerate(scores) if s > 0),
        key=lambda i: (-scores[i], i),
    )
    if not by_relevance:
        return [], False, 0
    best = by_relevance[0]
    least = scores[best] * floor
    matched = {i: set(terms) & set(_tokenize(chunks[i].text)) for i in by_relevance}
    relevant = [i for i in by_relevance if scores[i] >= least or matched[i] >= matched[best]]
    set_aside = len(by_relevance) - len(relevant)
    limit = max_tokens * _CHARS_PER_TOKEN
    chosen: list[int] = []
    used = 0
    for i in relevant:
        blen = len(chunks[i].text) + 2
        if chosen and used + blen > limit:
            break
        chosen.append(i)
        used += blen
    return (
        [Scored(chunks[i], scores[i]) for i in chosen],
        len(chosen) < len(relevant),
        set_aside,
    )


class _Span(NamedTuple):
    chunk: Chunk
    end: int
    text: str
    score: float


def _spans(chunks: list[Chunk], ranked: list[Scored]) -> list[_Span]:
    block_end = {c.block: c.offset + len(c.text) for c in chunks}
    block_start: dict[int, int] = {}
    for c in chunks:
        block_start.setdefault(c.block, c.offset)
    spans: list[_Span] = []
    for s in sorted(ranked, key=lambda s: s.chunk.offset):
        end = s.chunk.offset + len(s.chunk.text)
        last = spans[-1] if spans else None
        if last and last.chunk.block == s.chunk.block and last.end == s.chunk.offset:
            spans[-1] = _Span(last.chunk, end, last.text + s.chunk.text, max(last.score, s.score))
        else:
            spans.append(_Span(s.chunk, end, s.chunk.text, s.score))
    fenced: list[_Span] = []
    for span in spans:
        text, fence = span.text, span.chunk.fence
        if fence is not None:
            opening = _FENCE_OPEN_RE.match(fence)
            assert opening
            if span.chunk.offset != block_start[span.chunk.block]:
                text = fence + "\n" + text
            if span.end != block_end[span.chunk.block]:
                text = text.rstrip("\n") + "\n" + opening.group(1)
        fenced.append(span._replace(text=text))
    return fenced


def select(markdown: str, query: str, max_tokens: int) -> tuple[str, list[Match], bool, int]:
    chunks = chunk_document(markdown)
    ranked, truncated, set_aside = rank(chunks, query, max_tokens, floor=_RELEVANCE_FLOOR)
    if not ranked:
        return "", [], False, 0
    # Within one document, offset order is reading order — what a caller wants to
    # read back, unlike the relevance order `rank` returns.
    spans = _spans(chunks, ranked)
    matches: list[Match] = [
        {
            "heading": span.chunk.heading,
            "offset": span.chunk.offset,
            "score": round(span.score, SCORE_DIGITS),
        }
        for span in spans
    ]
    body = "\n\n".join(span.text for span in spans)
    return body, matches, truncated, set_aside
