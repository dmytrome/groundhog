import pytest

from groundhog_mcp import server
from groundhog_mcp.tools.read_url import read_url


async def test_maps_document_fields_onto_the_result(fake_provider, make_page):
    # The Document's own behaviour is covered in test_document.py; this guards the
    # mapping itself, e.g. url and final_url being swapped.
    span = {"reason": "display:none", "text": "PAYLOAD", "path": "div"}
    fake_provider(make_page(hidden=[span]))
    result = await read_url("https://ex.com/asked-for")
    assert result["url"] == "https://ex.com/asked-for"
    assert result["final_url"] == "https://ex.com/p"
    assert result["title"] == "Doc"
    assert result["threats"][0]["excerpt"] == "PAYLOAD"
    assert len(result["provenance"]["content_hash"]) == 64
    assert result["fetched_at"].endswith("+00:00")


async def test_mcp_boundary_rejects_unknown_format():
    # Validation moved from a runtime check to the Literal on the tool signature,
    # so the schema — not Python — is what rejects a bad format. Assert the real
    # transport contract rather than trusting the annotation.
    # A loopback URL the SSRF guard rejects on sight: if validation ever regresses,
    # this fails with "blocked address" instead of reaching the network.
    mcp = server.build_server()
    with pytest.raises(Exception, match="(?i)literal|enum|validation"):
        await mcp.call_tool("read_url", {"url": "http://127.0.0.1:1/", "format": "json"})


async def test_query_returns_only_relevant_passages(fake_provider, make_page):
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", query="loyal domesticated dogs")
    assert "loyal domesticated" in result["markdown"]
    assert "carnivorous mammals" not in result["markdown"]
    assert result["matches"] and result["matches"][0]["heading"] == "Dogs"


async def test_query_body_respects_token_budget(fake_provider, make_page):
    # A single matching chunk larger than the budget must still be truncated, and
    # `truncated` must report it — the query path honors max_tokens like the plain path.
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", query="loyal domesticated dogs", max_tokens=5)
    assert result["truncated"] is True
    assert "[... truncated]" in result["markdown"]


async def test_empty_query_uses_full_document(fake_provider, make_page):
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", query="   ")
    assert "Cats" in result["markdown"] and "Dogs" in result["markdown"]
    assert result["matches"] == []
