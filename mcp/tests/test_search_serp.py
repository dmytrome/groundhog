from pathlib import Path

import pytest

from groundhog_mcp.search import serp
from groundhog_mcp.search.types import SearchUnavailableError

# Captured through our own stealth browser from html.duckduckgo.com — real markup,
# including the //duckduckgo.com/l/?uddg= redirect wrapper around every result URL.
_SERP_HTML = (Path(__file__).parent / "fixtures" / "ddg_serp.html").read_text()


def test_parses_a_real_serp():
    hits = serp.parse(_SERP_HTML)
    assert len(hits) >= 5
    assert all(h["title"] for h in hits)
    assert all(h["engine"] == "duckduckgo" for h in hits)


def test_unwraps_the_redirect_to_the_real_url():
    hits = serp.parse(_SERP_HTML)
    # Every href on the page is a duckduckgo.com/l/ redirect; none may leak through.
    assert all(h["url"].startswith("http") for h in hits)
    assert not any("duckduckgo.com/l/" in h["url"] for h in hits)
    assert any("modelcontextprotocol.io" in h["url"] for h in hits)


def test_ranks_by_page_order_with_descending_scores():
    hits = serp.parse(_SERP_HTML)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_dangerous_schemes_in_the_redirect_are_dropped():
    # The uddg target is page content an attacker can plant.
    dangerous = ("javascript%3Aalert(1)", "file%3A%2F%2F%2Fetc%2Fpasswd", "data%3Atext%2Fhtml%2Cx")
    for payload in dangerous:
        assert serp._real_url(f"//duckduckgo.com/l/?uddg={payload}") is None


_SECOND_SNIPPET = (
    "MCP (Model Context Protocol) is an open-source standard for connecting AI applications "
    "to external systems. Using MCP, AI applications like Claude or ChatGPT can connect to "
    "data sources (e.g. local files, databases), tools (e.g. search engines, calculators) and "
    "workflows (e.g. specialized prompts)\u2014enabling them to access key information and "
    "perform tasks. Think of MCP like a USB-C port for ..."
)


def test_each_result_carries_its_own_snippet():
    hits = serp.parse(_SERP_HTML)

    assert hits[0]["snippet"] == "Model Context Protocol"
    assert hits[1]["snippet"] == _SECOND_SNIPPET


def test_a_missing_snippet_does_not_shift_later_snippets():
    without_first = _SERP_HTML.replace('class="result__snippet"', "class=gone", 1)

    hits = serp.parse(without_first)

    assert hits[0]["snippet"] == ""
    assert hits[1]["snippet"] == _SECOND_SNIPPET


def test_stale_selectors_raise_instead_of_reporting_no_results():
    broken = _SERP_HTML.replace("result__body", "result__gone")
    with pytest.raises(SearchUnavailableError, match="layout"):
        serp.parse(broken)


def test_genuine_no_results_page_is_empty_not_an_error():
    assert serp.parse('<html><body><div class="no-results">No results.</div></body></html>') == []
