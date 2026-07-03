import math
import re
from dataclasses import dataclass
from typing import TypedDict

_CHARS_PER_TOKEN = 4
_WORD_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+")
_K1 = 1.5
_B = 0.75


class Match(TypedDict):
    heading: str | None
    offset: int
    score: float


@dataclass
class _Chunk:
    heading: str | None
    offset: int
    text: str


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _chunk(markdown: str) -> list[_Chunk]:
    # Scan line by line so a heading with no blank line before its body still
    # splits into a heading + a searchable body chunk (a blank-line-delimited
    # block would swallow the body into the heading and drop it).
    chunks: list[_Chunk] = []
    heading: str | None = None
    lines: list[str] = []
    offset = 0
    pos = 0

    def flush() -> None:
        nonlocal lines
        if lines:
            chunks.append(_Chunk(heading=heading, offset=offset, text="\n".join(lines)))
            lines = []

    for raw in markdown.splitlines(keepends=True):
        line = raw.rstrip("\n")
        start = pos
        pos += len(raw)
        if not line.strip():
            flush()
        elif _HEADING_RE.match(line):
            flush()
            heading = _HEADING_RE.sub("", line).strip()
        else:
            if not lines:
                offset = start
            lines.append(line)
    flush()
    return chunks


def _bm25(chunks: list[_Chunk], query_terms: list[str]) -> list[float]:
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


def select(markdown: str, query: str, max_tokens: int) -> tuple[str, list[Match], bool]:
    chunks = _chunk(markdown)
    scores = _bm25(chunks, _tokenize(query))
    ranked = sorted(
        (i for i, s in enumerate(scores) if s > 0),
        key=lambda i: (-scores[i], i),
    )
    if not ranked:
        return "", [], False
    limit = max_tokens * _CHARS_PER_TOKEN
    chosen: list[int] = []
    used = 0
    for i in ranked:
        blen = len(chunks[i].text) + 2
        if chosen and used + blen > limit:
            break
        chosen.append(i)
        used += blen
    truncated = len(chosen) < len(ranked)
    chosen.sort()
    matches: list[Match] = [
        {"heading": chunks[i].heading, "offset": chunks[i].offset, "score": round(scores[i], 4)}
        for i in chosen
    ]
    body = "\n\n".join(chunks[i].text for i in chosen)
    return body, matches, truncated
