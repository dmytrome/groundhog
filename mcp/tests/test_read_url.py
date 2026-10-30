from collections.abc import Callable

import pytest

from groundhog_mcp import document, engine, extract, retrieval, safety, sanitize, server
from groundhog_mcp.tools.read_url import read_url

from .conftest import TAG_I, ZERO_WIDTH


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


async def test_a_status_that_is_not_a_real_code_is_reported_as_absent(fake_provider, make_page):
    # `0` is what a service-worker-synthesized or client-blocked request reports. The
    # documented contract is "the status code, or null when it could not be observed",
    # so handing back 0 would give a caller a number that is not a status.
    fake_provider(make_page(http_status=0, retrieval_status="unknown"))
    result = await read_url("https://ex.com/p")
    assert result["http_status"] is None
    assert result["status"] == "unknown"


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
    with pytest.raises(Exception, match=r"(?i)literal|enum|validation"):
        await mcp.call_tool("read_url", {"url": "http://127.0.0.1:1/", "format": "json"})


async def test_query_returns_only_relevant_passages(fake_provider, make_page):
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", query="loyal domesticated dogs")
    assert "loyal domesticated" in result["markdown"]
    assert "carnivorous mammals" not in result["markdown"]
    assert result["matches"] and result["matches"][0]["heading"] == "Dogs"


async def test_a_match_is_identified_by_the_final_url_and_the_page_snapshot(
    fake_provider, make_page
):
    fake_provider(make_page())

    result = await read_url("https://ex.com/asked-for", query="loyal domesticated dogs")

    match = result["matches"][0]
    assert match["id"] == retrieval.passage_id(
        "https://ex.com/p", result["provenance"]["content_hash"], match["offset"], match["end"]
    )


async def test_query_body_respects_token_budget(fake_provider, make_page):
    fake_provider(make_page())
    result = await read_url("https://ex.com/p", query="loyal domesticated dogs", max_tokens=5)
    assert result["truncated"] is True
    assert "[... truncated]" in result["markdown"]


@pytest.mark.parametrize("query", [None, "loyal domesticated dogs"])
async def test_a_text_body_is_cut_to_the_token_budget(
    fake_provider: Callable[[engine.RenderedPage], None],
    make_page: Callable[..., engine.RenderedPage],
    query: str | None,
) -> None:
    fake_provider(make_page(text="Dogs are loyal domesticated animals. " * 10))
    result = await read_url("https://ex.com/p", format="text", query=query, max_tokens=5)
    assert result["truncated"] is True
    assert result["markdown"].endswith("[... truncated]")
    assert 0 < len(result["markdown"].removesuffix("\n\n[... truncated]")) <= 20


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


async def test_page_chosen_errors_are_cleaned_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(url: str, **kwargs: object) -> document.Document:
        raise RuntimeError(f"boom{ZERO_WIDTH}{TAG_I}" + "x" * 4000)

    monkeypatch.setattr(document, "fetch_document", _boom)

    with pytest.raises(safety.CallerFacingError) as excinfo:
        await read_url("https://ex.com/p")

    detail = str(excinfo.value)
    assert detail.startswith("RuntimeError: boom")
    assert ZERO_WIDTH not in detail and TAG_I not in detail
    assert len(detail) <= sanitize.MAX_ERROR_CHARS


async def test_an_oversized_page_is_read_as_too_large_with_no_content(fake_provider, make_page):
    fake_provider(make_page(text="Cats nap. " * (sanitize.MAX_MARKDOWN_BYTES // 10 + 1)))

    result = await read_url("https://ex.com/p", format="text", query="cats")

    assert result["status"] == "too_large"
    assert result["markdown"] == ""
    assert result["matches"] == []


async def test_a_match_describes_exactly_the_text_returned(
    fake_provider: Callable[[engine.RenderedPage], None],
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    text = "Cats nap often in the sun and dream of mice. " * 8
    fake_provider(make_page(text=text))

    result = await read_url("https://ex.com/p", format="text", query="cats nap", max_tokens=5)

    kept = result["markdown"].removesuffix(extract.TRUNCATION_MARKER)
    [match] = result["matches"]
    assert text[match["offset"] : match["end"]] == kept
    assert match["clamped"] and result["truncated"]


async def test_a_matching_code_block_too_big_to_return_is_left_out_whole(
    fake_provider: Callable[[engine.RenderedPage], None],
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    fake_provider(make_page(text="Intro.\n\n```\n" + "needle()\n" * 20 + "```"))

    result = await read_url("https://ex.com/p", format="text", query="needle", max_tokens=10)

    assert (result["markdown"], result["matches"], result["truncated"]) == ("", [], True)
