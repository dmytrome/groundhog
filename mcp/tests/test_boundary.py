"""The untrusted-input boundary, asserted over a whole `read_url` result.

Five review passes each found a *different* page-authored field reaching the model
unsanitized — the signature of a rule kept by discipline. These tests walk the
result instead of naming fields, so a field added to that path is caught without
anyone remembering to add a case for it. `research` composes the same `Document`,
but its own fields (`sources[].error`, `sources[].title`) are not walked here.
"""

import dataclasses
import typing

from groundhog_mcp import sanitize
from groundhog_mcp.engine import RenderedPage
from groundhog_mcp.tools.read_url import read_url

from .conftest import INVISIBLES, RTL_OVERRIDE, TAG_I, ZERO_WIDTH

POISON = f"{ZERO_WIDTH}{RTL_OVERRIDE}{TAG_I}"
# Longer than every cap the boundary applies, so an unbounded field stands out.
LONG = "z" * 9000
# The one field legitimately sized by the caller's token budget, not by a field cap.
BUDGETED = {"markdown"}
# Carry the content itself; stripped downstream with threat collection, not here.
_CONTENT_FIELDS = {"html", "text"}


def _strings(value: object, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _strings(v, f"{path}[{i}]")]
    return []


def _poisoned_page(make_page):
    """A page with every field the browser produces carrying a payload.

    The `str` fields are derived from `dataclasses.fields(RenderedPage)` rather than
    listed here: a field added to that class and forgotten in `__post_init__` is then
    poisoned automatically, and the walks below see it. Hand-listing them meant a new
    field was never poisoned, so the tests could not catch the omission they exist for.
    """
    page = make_page(
        hidden=[{"reason": f"display:none{POISON}", "text": POISON + LONG, "path": POISON + LONG}],
        meta={
            "meta": {"author": POISON + LONG, "article:published_time": POISON + LONG},
            "lang": POISON + LONG,
            "canonical": "https://ex.com/c" + POISON + LONG,
        },
    )
    hints = typing.get_type_hints(RenderedPage)
    poisoned = [
        field.name
        for field in dataclasses.fields(RenderedPage)
        if hints.get(field.name) is str and field.name not in _CONTENT_FIELDS
    ]
    # Fail closed. `dataclasses.Field.type` is the annotation as written, so it becomes
    # the *string* "str" the moment this module gains `from __future__ import
    # annotations` — every match would then silently be False, nothing would be
    # poisoned, and both tests below would pass while asserting nothing. Resolving via
    # `get_type_hints` avoids that, and this guard catches it if the resolution ever
    # stops working.
    assert poisoned, "no str fields poisoned — these tests would pass vacuously"
    for name in poisoned:
        # Re-run through the boundary so the poison is actually sanitized on the way
        # in, exactly as a real page's value would be.
        setattr(page, name, POISON + LONG)
    return RenderedPage(**{f.name: getattr(page, f.name) for f in dataclasses.fields(page)})


async def test_no_invisible_characters_survive_anywhere_in_a_read_url_result(
    fake_provider, make_page
):
    fake_provider(_poisoned_page(make_page))
    result = await read_url("https://ex.com/p")
    offenders = [path for path, text in _strings(result) if any(ch in text for ch in INVISIBLES)]
    assert offenders == [], f"invisible characters reached the model at: {offenders}"


async def test_every_string_in_a_read_url_result_is_bounded(fake_provider, make_page):
    fake_provider(_poisoned_page(make_page))
    result = await read_url("https://ex.com/p")
    unbounded = [
        (path, len(text))
        for path, text in _strings(result)
        if path.rsplit(".", 1)[-1] not in BUDGETED and len(text) > sanitize.MAX_URL_CHARS
    ]
    assert unbounded == [], f"unbounded page-authored strings reached the model: {unbounded}"
