import math
import re
from dataclasses import dataclass
from typing import NamedTuple, TypedDict

from . import sanitize

_CHARS_PER_TOKEN = 4
_WORD_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+")
SCORE_DIGITS = 4  # one reported precision for BM25 scores, wherever they surface
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


class Scored(NamedTuple):
    chunk: Chunk
    score: float


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def chunk_document(markdown: str, source: str | None = None) -> list[Chunk]:
    # Scan line by line so a heading with no blank line before its body still
    # splits into a heading + a searchable body chunk (a blank-line-delimited
    # block would swallow the body into the heading and drop it).
    chunks: list[Chunk] = []
    heading: str | None = None
    lines: list[str] = []
    offset = 0
    pos = 0

    def flush() -> None:
        nonlocal lines
        if lines:
            chunks.append(
                Chunk(heading=heading, offset=offset, text="\n".join(lines), source=source)
            )
            lines = []

    for raw in markdown.splitlines(keepends=True):
        line = raw.rstrip("\n")
        start = pos
        pos += len(raw)
        if not line.strip():
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
                offset = start
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


def rank(chunks: list[Chunk], query: str, max_tokens: int) -> tuple[list[Scored], bool]:
    """Score passages against `query` and admit the best that fit the budget."""
    scores = _bm25(chunks, _tokenize(query))
    by_relevance = sorted(
        (i for i, s in enumerate(scores) if s > 0),
        key=lambda i: (-scores[i], i),
    )
    if not by_relevance:
        return [], False
    limit = max_tokens * _CHARS_PER_TOKEN
    chosen: list[int] = []
    used = 0
    for i in by_relevance:
        blen = len(chunks[i].text) + 2
        if chosen and used + blen > limit:
            break
        chosen.append(i)
        used += blen
    return [Scored(chunks[i], scores[i]) for i in chosen], len(chosen) < len(by_relevance)


def select(markdown: str, query: str, max_tokens: int) -> tuple[str, list[Match], bool]:
    ranked, truncated = rank(chunk_document(markdown), query, max_tokens)
    if not ranked:
        return "", [], False
    # Within one document, offset order is reading order — what a caller wants to
    # read back, unlike the relevance order `rank` returns.
    by_offset = sorted(ranked, key=lambda s: s.chunk.offset)
    matches: list[Match] = [
        {
            "heading": s.chunk.heading,
            "offset": s.chunk.offset,
            "score": round(s.score, SCORE_DIGITS),
        }
        for s in by_offset
    ]
    body = "\n\n".join(s.chunk.text for s in by_offset)
    return body, matches, truncated
