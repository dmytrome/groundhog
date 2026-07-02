import pytest

from groundhog_mcp import engine
from groundhog_mcp.engine import RenderedPage
from groundhog_mcp.tools.read_url import read_url

# No <meta name="author"> here — the engine's JS collector provides it via engine_meta,
# not via the HTML. trafilatura does not surface it reliably for short articles.
PAGE_HTML = """
<html lang="en"><head><title>Doc</title></head>
<body><article>
<h1>Cats</h1><p>Cats are small carnivorous mammals kept as pets worldwide indeed.</p>
<h2>Dogs</h2><p>Dogs are loyal domesticated animals trained for work and company.</p>
</article></body></html>
"""


class _FakeProvider:
    def __init__(self, page):
        self._page = page

    async def fetch(self, url, strip_hidden=True):
        return self._page


@pytest.fixture
def fake_provider(monkeypatch):
    def _install(page):
        async def _get():
            return _FakeProvider(page)
        monkeypatch.setattr(engine, "get_provider", _get)
    return _install


_DEFAULT_META = {"meta": {"author": "A. Writer"}, "lang": "en", "canonical": None}


def _page(html=PAGE_HTML, hidden=None, meta=None):
    return RenderedPage(
        html=html, text="unused", final_url="https://ex.com/p", title="Doc",
        hidden_spans=hidden or [], meta=meta or _DEFAULT_META,
    )


async def test_rejects_unknown_format():
    with pytest.raises(ValueError):
        await read_url("https://ex.com/", format="json")


async def test_result_has_new_fields_and_provenance(fake_provider):
    fake_provider(_page())
    result = await read_url("https://ex.com/p")
    assert result["threats"] == []
    assert result["matches"] == []
    assert result["provenance"]["word_count"] > 0
    assert len(result["provenance"]["content_hash"]) == 64
    assert result["provenance"]["author"] == "A. Writer"


async def test_hidden_spans_become_threats(fake_provider):
    span = {"text": "SECRET", "reason": "display:none/visibility:hidden", "path": "div"}
    fake_provider(_page(hidden=[span]))
    result = await read_url("https://ex.com/p")
    assert result["threats"][0]["type"] == "hidden_css"
    assert result["threats"][0]["excerpt"] == "SECRET"
    assert result["threats"][0]["location"] == "div"


async def test_query_returns_only_relevant_passages(fake_provider):
    fake_provider(_page())
    result = await read_url("https://ex.com/p", query="loyal domesticated dogs")
    assert "loyal domesticated" in result["markdown"]
    assert "carnivorous mammals" not in result["markdown"]
    assert result["matches"] and result["matches"][0]["heading"] == "Dogs"


async def test_empty_query_uses_full_document(fake_provider):
    fake_provider(_page())
    result = await read_url("https://ex.com/p", query="   ")
    assert "Cats" in result["markdown"] and "Dogs" in result["markdown"]
    assert result["matches"] == []
