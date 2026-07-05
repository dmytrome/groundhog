from groundhog_mcp.extract import ExtractMeta
from groundhog_mcp.provenance import build


def _engine_meta(meta=None, lang=None, canonical=None):
    return {"meta": meta or {}, "lang": lang, "canonical": canonical}


def test_hash_and_word_count_always_present():
    prov = build("one two three", ExtractMeta(None, None, None), _engine_meta())
    assert len(prov["content_hash"]) == 64
    assert prov["word_count"] == 3


def test_prefers_trafilatura_then_falls_back_to_meta():
    prov = build(
        "text",
        ExtractMeta(author=None, published=None, canonical=None),
        _engine_meta(
            meta={"article:author": "Meta Author", "article:published_time": "2023-05-06"}
        ),
    )
    assert prov["author"] == "Meta Author"
    assert prov["published"] == "2023-05-06"


def test_trafilatura_wins_over_meta():
    prov = build(
        "text",
        ExtractMeta(author="Traf Author", published=None, canonical=None),
        _engine_meta(meta={"author": "Meta Author"}),
    )
    assert prov["author"] == "Traf Author"


def test_implausible_trafilatura_author_is_rejected():
    # trafilatura scrapes Wikipedia's authority-control box as the "author".
    junk = "Authority control databases National United States Israel Other Yale LUX"
    to_meta = build(
        "text",
        ExtractMeta(author=junk, published=None, canonical=None),
        _engine_meta(meta={"article:author": "Real Byline"}),
    )
    assert to_meta["author"] == "Real Byline"
    to_none = build(
        "text", ExtractMeta(author=junk, published=None, canonical=None), _engine_meta()
    )
    assert to_none["author"] is None


def test_modified_from_meta_only_and_null_when_absent():
    with_mod = build(
        "t",
        ExtractMeta(None, None, None),
        _engine_meta(meta={"article:modified_time": "2024-09-09"}),
    )
    assert with_mod["modified"] == "2024-09-09"
    without = build("t", ExtractMeta(None, None, None), _engine_meta())
    assert without["modified"] is None


def test_language_detected_from_content():
    english = "This is clearly an English sentence about grounding web content for agents."
    prov = build(english, ExtractMeta(None, None, None), _engine_meta())
    assert prov["language"] == "en"


def test_language_falls_back_to_hint_for_short_text():
    prov = build("hi", ExtractMeta(None, None, None), _engine_meta(lang="de"))
    assert prov["language"] == "de"
