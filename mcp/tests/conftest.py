import pytest

from groundhog_mcp import classify, engine
from groundhog_mcp.engine import HiddenSpan, PageMeta, RenderedPage

# No <meta name="author"> here — the engine's JS collector provides it via engine_meta,
# not via the HTML. trafilatura does not surface it reliably for short articles.
_PAGE_HTML = """
<html lang="en"><head><title>Doc</title></head>
<body><article>
<h1>Cats</h1><p>Cats are small carnivorous mammals kept as pets worldwide indeed.</p>
<h2>Dogs</h2><p>Dogs are loyal domesticated animals trained for work and company.</p>
</article></body></html>
"""

_DEFAULT_META: PageMeta = {"meta": {"author": "A. Writer"}, "lang": "en", "canonical": None}

# Written as escapes, not literals: these are invisible in an editor and in a diff,
# so a stray normalization would otherwise gut a test without anyone noticing.
ZERO_WIDTH = "\u200b"
RTL_OVERRIDE = "\u202e"
TAG_I = "\U000e0049"  # Unicode Tag block: an invisible ASCII mirror
INVISIBLES = (ZERO_WIDTH, RTL_OVERRIDE, TAG_I)


class _FakeProvider:
    def __init__(self, page: RenderedPage) -> None:
        self._page = page

    async def fetch(self, url: str, strip_hidden: bool = True) -> RenderedPage:
        return self._page


@pytest.fixture
def make_page():
    def _make(
        hidden: list[HiddenSpan] | None = None,
        *,
        title: str = "Doc",
        text: str = "unused",
        final_url: str = "https://ex.com/p",
        meta: PageMeta | None = None,
        isolated: bool = True,
        strip_incomplete: bool = False,
        http_status: int | None = 200,
        retrieval_status: classify.RetrievalStatus = "ok",
    ) -> RenderedPage:
        return RenderedPage(
            html=_PAGE_HTML,
            text=text,
            final_url=final_url,
            title=title,
            hidden_spans=hidden or [],
            meta=meta or _DEFAULT_META,
            isolated=isolated,
            strip_incomplete=strip_incomplete,
            http_status=http_status,
            retrieval_status=retrieval_status,
        )

    return _make


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch):
    def _install(page: RenderedPage) -> None:
        async def _get() -> _FakeProvider:
            return _FakeProvider(page)

        monkeypatch.setattr(engine, "get_provider", _get)

    return _install
