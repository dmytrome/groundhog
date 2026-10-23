import os

import pytest

from groundhog_mcp import engine
from groundhog_mcp.tools.read_url import ReadResult, read_url

from .conftest import unescaped
from .test_engine_live import PAGE_HOST, _serve_paths

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="requires the engine running; set RUN_LIVE=1 and CDP_URL",
)


@pytest.fixture(autouse=True)
async def _close_provider():
    # read_url uses a lazy singleton provider; close it after each test so the
    # CDP connection does not keep the process alive.
    yield
    await engine.shutdown_provider()


async def test_read_url_returns_markdown_and_provenance():
    result = await read_url("https://example.com/")
    assert "Example Domain" in result["title"]
    assert "documentation examples" in result["markdown"]
    assert result["url"] == "https://example.com/"
    assert result["final_url"].startswith("https://example.com")
    assert result["fetched_at"].endswith("+00:00")
    assert result["truncated"] is False


async def test_read_url_text_format():
    result = await read_url("https://example.com/", format="text")
    assert "documentation examples" in result["markdown"]


async def test_read_url_populates_provenance_and_threats():
    result = await read_url("https://example.com/")
    assert isinstance(result["threats"], list)
    assert result["matches"] == []
    assert len(result["provenance"]["content_hash"]) == 64


async def test_read_url_detects_the_language_of_the_page_it_read(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    await engine.shutdown_provider()
    paragraph = (
        "Tomatoes need full sun, steady watering and a sturdy stake once the first trusses "
        "set fruit. Pinch out the side shoots every week so the plant puts its strength "
        "into the fruit rather than the leaves. "
    )
    article = (
        "<html><body><article><h1>Growing tomatoes</h1>"
        f"<p>{paragraph * 3}</p></article></body></html>"
    )
    srv = _serve_paths({"/": (200, article)})
    try:
        result = await read_url(f"http://{PAGE_HOST}:{srv.server_address[1]}/")
    finally:
        srv.shutdown()

    assert result["provenance"]["language"] == "en"


_HIDING_MARKUP = {
    "INLINE_DISPLAY_NONE": '<div style="display:none">{m}</div>',
    "HIDDEN_ATTRIBUTE": "<div hidden>{m}</div>",
    "ARIA_HIDDEN_CLASS": '<div class="visually-hidden" aria-hidden="true" '
    'style="position:absolute;left:-9999px">{m}</div>',
}


@pytest.mark.parametrize("fmt", ["markdown", "text"])
async def test_include_hidden_puts_hidden_text_back_into_the_content(
    monkeypatch: pytest.MonkeyPatch, fmt: str
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    await engine.shutdown_provider()
    paragraph = (
        "The board approved a dividend of forty cents per share this quarter, citing steady "
        "demand across every region in which it operates. "
    )
    carriers = "".join(markup.format(m=marker) for marker, markup in _HIDING_MARKUP.items())
    article = (
        "<html><body><article><h1>Quarterly results</h1>"
        f"<p>{paragraph * 3}</p>{carriers}<p>{paragraph * 2}</p></article></body></html>"
    )
    srv = _serve_paths({"/": (200, article)})
    url = f"http://{PAGE_HOST}:{srv.server_address[1]}/"
    try:
        stripped = await read_url(url, format=fmt)
        kept = await read_url(url, format=fmt, include_hidden=True)
    finally:
        srv.shutdown()

    assert [m for m in _HIDING_MARKUP if m in unescaped(stripped["markdown"])] == []
    assert [m for m in _HIDING_MARKUP if m in unescaped(kept["markdown"])] == list(_HIDING_MARKUP)
    assert len(kept["threats"]) == len(stripped["threats"]) >= len(_HIDING_MARKUP)


async def _read_served(
    monkeypatch: pytest.MonkeyPatch, page: str, **kwargs: str | bool
) -> ReadResult:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    await engine.shutdown_provider()
    srv = _serve_paths({"/": (200, page)})
    try:
        return await read_url(f"http://{PAGE_HOST}:{srv.server_address[1]}/", **kwargs)
    finally:
        srv.shutdown()


_DIVIDEND = (
    "The board approved a dividend of forty cents per share this quarter, citing steady "
    "demand across every region in which it operates. "
)


async def test_include_hidden_keeps_hidden_text_inside_a_shadow_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = f"""<html><body><article><h1>Quarterly results</h1><p>{_DIVIDEND * 3}</p>
<div id="host"></div><p>{_DIVIDEND * 2}</p></article><script>
document.getElementById('host').attachShadow({{mode: 'open'}}).innerHTML =
  '<div style="display:none"><p class="note hidden">SHADOW_KEPT</p></div>';
</script></body></html>"""

    kept = await _read_served(monkeypatch, page, include_hidden=True)

    assert "SHADOW_KEPT" in unescaped(kept["markdown"])


async def test_include_hidden_leaves_layout_text_alone_when_only_scripts_are_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = (
        "<html><body><article><h1>Quarterly results</h1>"
        f"<p>{_DIVIDEND}</p><p style='display:inline'>Inline one.</p>"
        "<p style='display:inline'>Inline two.</p>"
        "<script>window.analytics = 1;</script></article></body></html>"
    )

    default = await _read_served(monkeypatch, page, format="text")
    kept = await _read_served(monkeypatch, page, format="text", include_hidden=True)

    assert kept["markdown"] == default["markdown"]


async def test_include_hidden_keeps_the_byline_the_extractor_finds_by_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = f"""<html><body><article><h1>Quarterly results</h1>
<div style="display:none"><p class="byline">By <span class="author">Ada Lovelace</span></p>
<p>HIDDEN_NOTE</p></div><p>{_DIVIDEND * 3}</p><p>{_DIVIDEND * 2}</p></article></body></html>"""

    kept = await _read_served(monkeypatch, page, include_hidden=True)

    assert kept["provenance"]["author"] == "Ada Lovelace"
    assert "HIDDEN_NOTE" in unescaped(kept["markdown"])


_SHADOW_ONLY = (
    "<html><body><div id='host'></div><script>"
    "document.getElementById('host').attachShadow({mode: 'open'}).innerHTML ="
    " '<p>Tomatoes need full sun and steady watering.</p>';"
    "</script></body></html>"
)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("<html><body></body></html>", "empty"),
        ('<html><body><img src="/x.png" alt="a tomato"></body></html>', "empty"),
        ("<html><body><hr></body></html>", "empty"),
        (_SHADOW_ONLY, "ok"),
    ],
)
async def test_a_page_a_reader_sees_nothing_on_is_empty(
    monkeypatch: pytest.MonkeyPatch, body: str, status: str
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    await engine.shutdown_provider()
    srv = _serve_paths({"/": (200, body)})
    try:
        result = await read_url(f"http://{PAGE_HOST}:{srv.server_address[1]}/")
    finally:
        srv.shutdown()

    assert result["status"] == status
