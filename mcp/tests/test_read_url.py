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


async def test_result_carries_the_retrieval_status(fake_provider, make_page):
    fake_provider(make_page())
    result = await read_url("https://ex.com/p")
    assert result["status"] == "ok"
    assert result["http_status"] == 200


async def test_a_challenge_page_is_surfaced_not_returned_as_ok(fake_provider, make_page):
    # The whole point: a block/interstitial must reach the caller labelled, not as if
    # it were the page. The classification is computed in the engine; this guards that
    # it survives the Document -> ReadResult mapping.
    fake_provider(make_page(retrieval_status="challenge", http_status=403))
    result = await read_url("https://ex.com/p")
    assert result["status"] == "challenge"
    assert result["http_status"] == 403


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


@pytest.mark.parametrize("bad", [0, -1, -10_000])
async def test_nonpositive_max_tokens_falls_back_to_the_configured_budget(
    fake_provider, make_page, bad
):
    # A model-supplied budget reaches this boundary directly; a non-positive one
    # would otherwise flow into ranking, where the first passage is admitted
    # unconditionally and the budget check is skipped.
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", max_tokens=bad)
    default = await read_url("https://ex.com/p")
    assert result["markdown"] == default["markdown"]
    assert result["truncated"] is False


async def test_a_blocked_url_does_not_leak_the_resolved_address():
    # The SSRF guard's own message names the host and the address it resolved to.
    # `research` refuses to echo that; the single-page tool must not either.
    with pytest.raises(Exception) as excinfo:
        await read_url("http://localhost:1/")
    message = str(excinfo.value)
    assert "blocked by SSRF policy" in message
    assert "127.0.0.1" not in message and "::1" not in message
