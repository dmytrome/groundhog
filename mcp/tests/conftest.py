import pytest

from groundhog_mcp import engine
from groundhog_mcp.engine import HiddenSpan, RenderedPage

# No <meta name="author"> here — the engine's JS collector provides it via engine_meta,
# not via the HTML. trafilatura does not surface it reliably for short articles.
_PAGE_HTML = """
<html lang="en"><head><title>Doc</title></head>
<body><article>
<h1>Cats</h1><p>Cats are small carnivorous mammals kept as pets worldwide indeed.</p>
<h2>Dogs</h2><p>Dogs are loyal domesticated animals trained for work and company.</p>
</article></body></html>
"""

_DEFAULT_META = {"meta": {"author": "A. Writer"}, "lang": "en", "canonical": None}


class _FakeProvider:
    def __init__(self, page: RenderedPage) -> None:
        self._page = page

    async def fetch(self, url: str, strip_hidden: bool = True) -> RenderedPage:
        return self._page


@pytest.fixture
def make_page():
    def _make(hidden: list[HiddenSpan] | None = None) -> RenderedPage:
        return RenderedPage(
            html=_PAGE_HTML,
            text="unused",
            final_url="https://ex.com/p",
            title="Doc",
            hidden_spans=hidden or [],
            meta=_DEFAULT_META,
        )

    return _make


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch):
    def _install(page: RenderedPage) -> None:
        async def _get() -> _FakeProvider:
            return _FakeProvider(page)

        monkeypatch.setattr(engine, "get_provider", _get)

    return _install
