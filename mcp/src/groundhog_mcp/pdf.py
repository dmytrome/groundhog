import asyncio
import io
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Literal

import pypdf
import pypdf.filters
from pypdf.errors import LimitReachedError
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    ContentStream,
    DictionaryObject,
    FloatObject,
    NameObject,
    PdfObject,
    StreamObject,
    TextStringObject,
)
from pypdf.generic._font import Font

from . import sanitize

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 100
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_TEXT_CHARS = 2 * 1024 * 1024
MAX_RESULT_BYTES = 6 * MAX_TEXT_CHARS + 2 * sanitize.MAX_TITLE_CHARS + 4096
PARSE_TIMEOUT_S = 20.0
_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
HIDDEN_REASON = "pdf-render-mode"
_INVISIBLE_MODES = (3, 7)
_STRIP, _KEEP = "strip", "keep"
_EXTRACTION_FAILURES = ("Impossible to decode XFormObject", "Detected cyclic", "Exceeded ")

Status = Literal["ok", "too_large", "unsupported_content"]


@dataclass
class PdfText:
    status: Status
    pages: list[str] = field(default_factory=list)
    hidden: list[dict[str, str]] = field(default_factory=list)
    title: str | None = None


async def read(data: bytes, *, strip_hidden: bool) -> PdfText:
    if len(data) > MAX_PDF_BYTES:
        return PdfText("too_large")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        __name__,
        _STRIP if strip_hidden else _KEEP,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out = await asyncio.wait_for(_exchange(proc, data), PARSE_TIMEOUT_S)
    except TimeoutError:
        return PdfText("too_large")
    except (BrokenPipeError, ConnectionResetError):
        return PdfText("unsupported_content")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if out is None:
        return PdfText("too_large")
    return _parsed(out)


async def _exchange(proc: asyncio.subprocess.Process, data: bytes) -> bytes | None:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(data)
    await proc.stdin.drain()
    proc.stdin.close()
    out = bytearray()
    while chunk := await proc.stdout.read(1 << 16):
        out += chunk
        if len(out) > MAX_RESULT_BYTES:
            return None
    await proc.wait()
    return bytes(out)


def _parsed(out: bytes) -> PdfText:
    try:
        raw = json.loads(out.decode("utf-8", "surrogatepass"))
    except ValueError:
        return PdfText("unsupported_content")
    if not isinstance(raw, dict) or raw.get("status") not in ("ok", "too_large"):
        return PdfText("unsupported_content")
    pages = raw.get("pages")
    hidden = raw.get("hidden")
    title = raw.get("title")
    return PdfText(
        raw["status"],
        pages=[p for p in pages if isinstance(p, str)] if isinstance(pages, list) else [],
        hidden=[
            {"text": h["text"], "reason": HIDDEN_REASON, "path": h["path"]}
            for h in (hidden if isinstance(hidden, list) else [])
            if isinstance(h, dict) and isinstance(h.get("text"), str)
            if isinstance(h.get("path"), str)
        ],
        title=title if isinstance(title, str) else None,
    )


class _Unmeasurable(Exception):
    pass


class _Failed(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.failed = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "pypdf.filters" or record.getMessage().startswith(_EXTRACTION_FAILURES):
            self.failed = True


@dataclass(frozen=True)
class _FontInfo:
    widths: dict[str, float]
    default: float
    scale: float
    composite: bool


def _value(entry: PdfObject | None) -> PdfObject | None:
    return entry.get_object() if entry is not None else None


def _font(entry: PdfObject | None) -> DictionaryObject | None:
    font = _value(entry)
    return font if isinstance(font, DictionaryObject) else None


@dataclass
class _State:
    font: DictionaryObject | None = None
    size: float = 0.0
    tc: float = 0.0
    tw: float = 0.0
    tr: int = 0

    def copy(self) -> "_State":
        return _State(self.font, self.size, self.tc, self.tw, self.tr)

    def key(self) -> tuple[int, float, float, float, int]:
        return (id(self.font), self.size, self.tc, self.tw, self.tr)

    def hidden(self) -> bool:
        type3 = self.font is not None and _value(self.font.get("/Subtype")) == "/Type3"
        return self.tr in _INVISIBLE_MODES and not type3


def _font_info(font: DictionaryObject) -> _FontInfo:
    subtype = _value(font.get("/Subtype"))
    composite = subtype == "/Type0"
    if composite and _value(font.get("/Encoding")) != "/Identity-H":
        raise _Unmeasurable
    try:
        parsed = Font.from_font_resource(font)
    except (LimitReachedError, pypdf.errors.PdfReadError, KeyError, TypeError, ValueError):
        raise _Unmeasurable from None
    matrix = _value(font.get("/FontMatrix"))
    type3 = subtype == "/Type3"
    scale = float(matrix[0]) if type3 and isinstance(matrix, list) and matrix else 0.001
    return _FontInfo(
        dict(parsed.character_widths),
        float(parsed.character_widths.get("default", 0)),
        scale,
        composite,
    )


def _codes(operand: PdfObject, font: _FontInfo) -> list[int]:
    raw = bytes(operand) if isinstance(operand, bytes) else b""
    if font.composite:
        return [raw[i] << 8 | raw[i + 1] for i in range(0, len(raw) - 1, 2)]
    return list(raw)


def _advance(parts: list[PdfObject], font: _FontInfo, state: _State) -> float:
    if not state.size:
        raise _Unmeasurable
    total = 0.0
    for part in parts:
        if isinstance(part, (int, float)):
            total += float(part)
            continue
        for code in _codes(part, font):
            width = font.widths.get(chr(code), font.default) * font.scale
            word = code == 32 and not font.composite
            spacing = state.tc + (state.tw if word else 0)
            total -= 1000 * (width + spacing / state.size)
    return total


def _as_bytes(operand: PdfObject) -> PdfObject:
    if isinstance(operand, TextStringObject):
        return ByteStringObject(operand.original_bytes)
    if isinstance(operand, ArrayObject):
        return ArrayObject(_as_bytes(part) for part in operand)
    return operand


class _Rewriter:
    def __init__(self, reader: pypdf.PdfReader, keep_hidden_runs: bool) -> None:
        self._reader = reader
        self._keep_hidden_runs = keep_hidden_runs
        self._forms: dict[tuple[int, tuple[int, float, float, float, int]], NameObject] = {}
        self._fonts: dict[int, _FontInfo] = {}
        self.found = False

    def _measured(self, font: DictionaryObject | None) -> _FontInfo:
        if font is None:
            raise _Unmeasurable
        if id(font) not in self._fonts:
            self._fonts[id(font)] = _font_info(font)
        return self._fonts[id(font)]

    def rewrite(
        self, stream: PdfObject, resources: DictionaryObject, state: _State
    ) -> ContentStream:
        content = ContentStream(stream, self._reader)
        fonts = resources.get("/Font")
        fonts = fonts.get_object() if fonts is not None else DictionaryObject()
        stack: list[_State] = []
        out: list[tuple[list[PdfObject], bytes]] = []
        for raw_operands, op in content.operations:
            operands = [_as_bytes(operand) for operand in raw_operands]
            if op == b"q":
                stack.append(state.copy())
            elif op == b"Q" and stack:
                state = stack.pop()
            elif op == b"Tf" and len(operands) == 2:
                state.font = _font(fonts.get(operands[0]))
                state.size = float(operands[1])
            elif op == b"gs" and operands:
                self._graphics_state(resources, fonts, operands[0], state)
            elif op == b"Tc" and operands:
                state.tc = float(operands[0])
            elif op == b"Tw" and operands:
                state.tw = float(operands[0])
            elif op == b"Tr" and operands:
                state.tr = int(operands[0])
            elif op in (b"Tj", b"TJ", b"'", b'"'):
                out += self._show(operands, op, state)
                continue
            elif op == b"Do" and operands:
                out.append(([self._form(resources, operands[0], state)], op))
                continue
            out.append((operands, op))
        content.operations = out
        return content

    def _graphics_state(
        self, resources: DictionaryObject, fonts: DictionaryObject, name: PdfObject, state: _State
    ) -> None:
        states = _value(resources.get("/ExtGState"))
        entry = _value(states.get(name)) if isinstance(states, DictionaryObject) else None
        font = _value(entry.get("/Font")) if isinstance(entry, DictionaryObject) else None
        if isinstance(font, list) and len(font) == 2:
            ref = font[0]
            state.font = _font(fonts.get(ref) if isinstance(ref, NameObject) else ref)
            state.size = float(font[1])

    def _show(
        self, operands: list[PdfObject], op: bytes, state: _State
    ) -> list[tuple[list[PdfObject], bytes]]:
        lead: list[tuple[list[PdfObject], bytes]] = []
        if op == b'"' and len(operands) == 3:
            state.tw, state.tc = float(operands[0]), float(operands[1])
            lead = [([operands[0]], b"Tw"), ([operands[1]], b"Tc")]
        if op in (b"'", b'"'):
            lead.append(([], b"T*"))
        hidden = state.hidden()
        self.found = self.found or hidden
        if hidden == self._keep_hidden_runs:
            return lead + [([operands[-1]], b"TJ" if op == b"TJ" else b"Tj")]
        parts = list(operands[-1]) if op == b"TJ" else [operands[-1]]
        try:
            gap = _advance(parts, self._measured(state.font), state)
        except _Unmeasurable:
            if self._keep_hidden_runs:
                return lead
            raise
        return lead + [([ArrayObject([FloatObject(gap)])], b"TJ")]

    def _form(self, resources: DictionaryObject, name: PdfObject, state: _State) -> PdfObject:
        xobjects = resources.get("/XObject")
        xobjects = xobjects.get_object() if xobjects is not None else None
        form = _value(xobjects.get(name)) if xobjects is not None else None
        if not isinstance(form, StreamObject) or _value(form.get("/Subtype")) == "/Image":
            return name
        key = (id(form), state.key())
        if key not in self._forms:
            number = len(self._forms)
            while NameObject(f"/Gh{number}") in xobjects:
                number += 1
            self._forms[key] = NameObject(f"/Gh{number}")
            own = form.get("/Resources")
            copy = self.rewrite(
                form, own.get_object() if own is not None else resources, state.copy()
            )
            for entry in ("/BBox", "/Matrix", "/Resources", "/Group"):
                if entry in form:
                    copy[NameObject(entry)] = form[entry]
            copy[NameObject("/Type")] = NameObject("/XObject")
            copy[NameObject("/Subtype")] = NameObject("/Form")
            xobjects[self._forms[key]] = copy
        return self._forms[key]


def _rewritten(
    reader: pypdf.PdfReader, page: pypdf.PageObject, contents: PdfObject, keep_hidden_runs: bool
) -> tuple[ContentStream, bool]:
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else DictionaryObject()
    rewriter = _Rewriter(reader, keep_hidden_runs)
    return rewriter.rewrite(contents, resources, _State()), rewriter.found


def _texts(reader: pypdf.PdfReader, page: pypdf.PageObject, strip_hidden: bool) -> tuple[str, str]:
    contents = page.get_contents()
    if contents is None:
        return "", ""
    hidden_only, found = _rewritten(reader, page, contents, keep_hidden_runs=True)
    if not found:
        return page.extract_text(extraction_mode="plain"), ""
    page[NameObject("/Contents")] = hidden_only
    hidden = page.extract_text(extraction_mode="plain")
    shown = (
        _rewritten(reader, page, contents, keep_hidden_runs=False)[0] if strip_hidden else contents
    )
    page[NameObject("/Contents")] = shown
    return page.extract_text(extraction_mode="plain"), hidden


_decode = pypdf.filters.decode_stream_data
_decoded = [0]


def _counted(stream: object) -> bytes:
    data = _decode(stream)
    _decoded[0] += len(data)
    if _decoded[0] > MAX_DECODED_BYTES:
        raise LimitReachedError(f"decoded streams exceeded {MAX_DECODED_BYTES} bytes")
    return data


def extract(data: bytes, *, strip_hidden: bool) -> dict[str, object]:
    _decoded[0] = 0
    pypdf.filters.decode_stream_data = _counted
    failures = _Failed()
    logging.getLogger("pypdf").addHandler(failures)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return {"status": "unsupported_content"}
        if len(reader.pages) > MAX_PAGES:
            return {"status": "too_large"}
        pages: list[str] = []
        hidden: list[dict[str, str]] = []
        for number, page in enumerate(reader.pages, 1):
            text, hidden_text = _texts(reader, page, strip_hidden)
            if excerpt := " ".join(hidden_text.split()):
                hidden.append({"text": excerpt, "path": f"page {number}"})
            pages.append(text)
            if sum(map(len, pages)) + sum(len(h["text"]) for h in hidden) > MAX_TEXT_CHARS:
                return {"status": "too_large"}
        title = reader.metadata.title if reader.metadata else None
        title = title[: sanitize.MAX_TITLE_CHARS] if isinstance(title, str) else None
        if _decoded[0] <= MAX_DECODED_BYTES and not failures.failed:
            return {"status": "ok", "pages": pages, "hidden": hidden, "title": title}
    except (LimitReachedError, MemoryError):
        return {"status": "too_large"}
    except Exception:
        pass
    finally:
        logging.getLogger("pypdf").removeHandler(failures)
        pypdf.filters.decode_stream_data = _decode
    return {"status": "too_large" if _decoded[0] > MAX_DECODED_BYTES else "unsupported_content"}


def _limit_memory() -> None:
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


if __name__ == "__main__":
    _limit_memory()
    result = extract(sys.stdin.buffer.read(MAX_PDF_BYTES + 1), strip_hidden=sys.argv[1] == _STRIP)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8", "surrogatepass"))
