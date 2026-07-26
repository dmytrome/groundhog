import json
from pathlib import Path

import pytest

from groundhog_mcp.search import searxng
from groundhog_mcp.search.types import SearchUnavailableError

# Captured from a real SearXNG 2026.7 instance (`?q=python+programming+language&format=json`)
# with `formats: [html, json]` enabled — the genuine wire shape, not a hand-built stub.
_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "searxng_response.json").read_text())


def test_parses_a_real_response():
    hits = searxng.parse(_FIXTURE)
    assert hits
    first = hits[0]
    assert first["url"].startswith("http")
    assert first["title"]
    assert isinstance(first["snippet"], str)
    assert first["engine"]


def test_keeps_result_order():
    hits = searxng.parse(_FIXTURE)
    assert [h["url"] for h in hits] == [r["url"] for r in _FIXTURE["results"]]


def test_drops_results_without_a_url():
    # `url` is `str | None` on SearXNG's MainResult; a URL-less entry can't be fetched.
    url_less = {"title": "no url", "content": "x", "engine": "e"}
    payload = {"results": [url_less, *_FIXTURE["results"]]}
    hits = searxng.parse(payload)
    assert all(h["url"] for h in hits)
    assert len(hits) == len(_FIXTURE["results"])


def test_tolerates_missing_optional_fields():
    payload = {"results": [{"url": "https://ex.com/", "title": "T"}]}
    hits = searxng.parse(payload)
    assert hits[0] == {
        "title": "T",
        "url": "https://ex.com/",
        "snippet": "",
        "engine": "",
        "score": 0.0,
        "published": None,
    }


def test_empty_results_is_not_an_error():
    assert searxng.parse({"results": []}) == []


def test_all_engines_down_is_an_error_not_an_empty_web():
    # SearXNG answers 200 with results:[] when every engine is rate-limited or
    # CAPTCHA'd; returning [] would tell the agent "nothing exists".
    payload = {"results": [], "unresponsive_engines": [["google", "CAPTCHA"]]}
    with pytest.raises(SearchUnavailableError, match="CAPTCHA"):
        searxng.parse(payload)


def test_envelope_without_results_is_a_protocol_error():
    with pytest.raises(SearchUnavailableError, match="results"):
        searxng.parse({"detail": "Not Found"})


async def test_build_url_encodes_query_and_requests_json():
    url = searxng.build_url("http://sx:8080", "cats & dogs")
    assert url.startswith("http://sx:8080/search?")
    assert "format=json" in url
    assert "cats+%26+dogs" in url or "cats%20%26%20dogs" in url


def test_real_fixture_carries_the_unresponsive_engines_field():
    # Guards the assumption the error path above depends on.
    assert "unresponsive_engines" in _FIXTURE
