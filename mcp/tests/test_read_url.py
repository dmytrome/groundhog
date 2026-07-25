from groundhog_mcp.tools.read_url import read_url


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
