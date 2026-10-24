import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import NamedTuple, TypedDict

from . import extract, sanitize

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
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t\r]*$")
SCORE_DIGITS = 4
RELEVANCE_FLOOR = 0.5
_MAX_CHUNK_CHARS = 2400
_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+\Z")
_SENTENCE_END_RE = re.compile(r"[.?!] |。")
_K1 = 1.5
_B = 0.75


class Match(TypedDict):
    id: str
    heading: str | None
    offset: int
    end: int
    score: float
    clamped: bool


def passage_id(source_url: str, content_hash: str, start: int, end: int) -> str:
    snapshot = hashlib.sha256(f"{source_url}\n{content_hash}".encode()).hexdigest()[:12]
    return f"p:{snapshot}:{start}-{end}"


@dataclass
class Chunk:
    heading: str | None
    offset: int
    text: str
    source: str | None = None
    block: int = 0
    fence: str | None = None


class Scored(NamedTuple):
    chunk: Chunk
    score: float


class Ranked(NamedTuple):
    chosen: list[Scored]
    left_out: int
    set_aside: int


def opens_fence(text: str) -> bool:
    return bool(_FENCE_OPEN_RE.match(text.split("\n", 1)[0]))


def is_fenced(chunk: Chunk) -> bool:
    return chunk.fence is not None or opens_fence(chunk.text)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    normalized = _SELECTORS_RE.sub("", unicodedata.normalize("NFKC", text))
    for run in _RUN_RE.findall(normalized.lower()):
        if _UNSPACED_RE.match(run):
            tokens += run
            tokens += (run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


def fenced_lines(lines: list[str]) -> dict[int, int]:
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

    inside: dict[int, int] = {}
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
        inside.update(dict.fromkeys(range(index, closed_at + 1), index))
        index = closed_at + 1
    return inside


def split_lines(markdown: str) -> list[str]:
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
    raws = split_lines(markdown)
    fenced = fenced_lines([raw.rstrip("\n") for raw in raws])
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
        whole_fence = len(pieces) > 1 and fenced.get(first) == first
        start = offset
        for piece in pieces:
            chunks.append(
                Chunk(
                    heading=heading,
                    offset=start,
                    text=piece,
                    source=source,
                    block=block,
                    fence=lines[0].rstrip("\r")[: sanitize.MAX_HEADING_CHARS]
                    if whole_fence
                    else None,
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
            if fenced[index] == index:
                flush()
            if not lines:
                offset, first = start, index
            lines.append(line)
            if fenced.get(index + 1) != fenced[index]:
                flush()
        elif not line.strip():
            flush()
        elif _HEADING_RE.match(line):
            flush()
            heading = _HEADING_RE.sub("", line).strip()[: sanitize.MAX_HEADING_CHARS]
        else:
            if not lines:
                offset, first = start, index
            lines.append(line)
    flush()
    return chunks


def _bm25(chunks: list[Chunk], query_terms: list[str]) -> list[float]:
    docs = [tokenize(c.text) for c in chunks]
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


def relevant(chunks: list[Chunk], query: str, *, floor: float = 0.0) -> tuple[list[Scored], int]:
    terms = tokenize(query)
    scores = _bm25(chunks, terms)
    by_relevance = sorted(
        (i for i, s in enumerate(scores) if s > 0),
        key=lambda i: (-scores[i], i),
    )
    if not by_relevance:
        return [], 0
    best = by_relevance[0]
    least = scores[best] * floor
    matched = {i: set(terms) & set(tokenize(chunks[i].text)) for i in by_relevance}
    kept = [i for i in by_relevance if scores[i] >= least or matched[i] >= matched[best]]
    return [Scored(chunks[i], scores[i]) for i in kept], len(by_relevance) - len(kept)


def rank(
    chunks: list[Chunk],
    query: str,
    max_tokens: int,
    *,
    floor: float = 0.0,
    whole_fences: bool = False,
) -> Ranked:
    found, set_aside = relevant(chunks, query, floor=floor)
    limit = max_tokens * _CHARS_PER_TOKEN
    chosen: list[Scored] = []
    used = 0
    for scored in found:
        fence = scored.chunk.fence
        blen = len(scored.chunk.text) + 2 + (2 * len(fence) + 2 if fence else 0)
        if (chosen or (whole_fences and is_fenced(scored.chunk))) and used + blen > limit:
            continue
        chosen.append(scored)
        used += blen
    return Ranked(chosen, len(found) - len(chosen), set_aside)


class _Span(NamedTuple):
    chunk: Chunk
    end: int
    text: str
    score: float


class _Layout(NamedTuple):
    starts: dict[int, int]
    ends: dict[int, int]
    fences: dict[int, str]


def _layout(chunks: list[Chunk]) -> _Layout:
    starts: dict[int, int] = {}
    fences: dict[int, str] = {}
    for c in chunks:
        starts.setdefault(c.block, c.offset)
        if c.fence is not None:
            fences[c.block] = c.fence
    return _Layout(starts, {c.block: c.offset + len(c.text) for c in chunks}, fences)


def _spans(layout: _Layout, ranked: list[Scored]) -> list[_Span]:
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
        text, fence = span.text, layout.fences.get(span.chunk.block)
        if fence is not None:
            opening = _FENCE_OPEN_RE.match(fence)
            assert opening
            if span.chunk.offset != layout.starts[span.chunk.block]:
                text = fence + "\n" + text
            if span.end != layout.ends[span.chunk.block]:
                text = text.rstrip("\n") + "\n" + opening.group(1)
        fenced.append(span._replace(text=text))
    return fenced


def _fitted(chunks: list[Chunk], found: list[Scored], limit: int) -> tuple[list[_Span], bool, int]:
    layout = _layout(chunks)
    kept: dict[int, list[Scored]] = {}
    sizes: dict[int, tuple[int, int]] = {}
    chars = count = 0
    for scored in found:
        block = scored.chunk.block
        trial = _spans(layout, kept.get(block, []) + [scored])
        size = (sum(len(span.text) for span in trial), len(trial))
        was = sizes.get(block, (0, 0))
        after = (chars - was[0] + size[0], count - was[1] + size[1])
        if after[0] + 2 * (after[1] - 1) <= limit:
            kept.setdefault(block, []).append(scored)
            sizes[block], (chars, count) = size, after
            continue
        if count or is_fenced(scored.chunk):
            continue
        text = extract.cut(scored.chunk.text, limit // _CHARS_PER_TOKEN)
        if not text:
            continue
        clipped = scored._replace(chunk=replace(scored.chunk, text=text))
        return _spans(layout, [clipped]), True, 1
    admitted = [s for block in kept.values() for s in block]
    return _spans(layout, admitted), False, len(admitted)


def select(
    markdown: str,
    query: str,
    max_tokens: int,
    *,
    source_url: str,
    content_hash: str,
) -> tuple[str, list[Match], bool, int]:
    chunks = chunk_document(markdown)
    found, set_aside = relevant(chunks, query, floor=RELEVANCE_FLOOR)
    if not found:
        return "", [], False, 0
    spans, clamped, admitted = _fitted(chunks, found, max_tokens * _CHARS_PER_TOKEN)
    matches: list[Match] = [
        {
            "id": passage_id(source_url, content_hash, span.chunk.offset, span.end),
            "heading": span.chunk.heading,
            "offset": span.chunk.offset,
            "end": span.end,
            "score": round(span.score, SCORE_DIGITS),
            "clamped": clamped,
        }
        for span in spans
    ]
    body = "\n\n".join(span.text for span in spans)
    return body, matches, clamped or admitted < len(found), set_aside
