from groundhog_mcp.extract import to_document, truncate

META_HTML = """
<html lang="en"><head><title>Real Title</title>
<meta name="author" content="Jane Doe">
<link rel="canonical" href="https://ex.com/canon">
<meta property="article:published_time" content="2024-01-02"></head>
<body><article><h1>Head</h1>
<p>First paragraph with enough words to be extracted by trafilatura here now.</p>
<p>Second paragraph so the extractor crosses its content threshold and returns.</p>
</article></body></html>
"""


def test_truncate_under_limit():
    text, cut = truncate("hello", 1000)
    assert text == "hello"
    assert cut is False


def test_truncate_over_limit_marks_and_cuts():
    text, cut = truncate("para one\n\npara two\n\npara three", 2)  # limit=8, no \n\n below it
    assert cut is True
    assert text.endswith("\n\n[... truncated]")


def test_truncate_cuts_at_paragraph_boundary():
    text, cut = truncate("para one\n\npara two\n\npara three", 3)  # limit=12, \n\n at index 8
    assert cut is True
    assert text == "para one\n\n[... truncated]"


def test_to_document_returns_markdown_and_metadata():
    markdown, meta = to_document(META_HTML, "https://ex.com/x")
    assert "First paragraph" in markdown
    assert meta.author == "Jane Doe"
    assert meta.published == "2024-01-02"
    assert meta.canonical == "https://ex.com/canon"


def test_to_document_metadata_nulls_when_absent():
    markdown, meta = to_document(
        "<html><body><article><p>" + "word " * 40 + "</p></article></body></html>",
        "https://x.com",
    )
    assert meta.author is None
    assert meta.canonical is None


def test_the_shared_page_fixture_extracts_with_the_structure_a_real_page_has():
    # Below a few hundred characters the extractor falls back to a bare-text path whose
    # output carries no headings and no paragraph breaks — on some versions and not
    # others. A fixture that small tests a path no real page takes, and silently changes
    # meaning when the extractor is upgraded.
    from .conftest import _PAGE_HTML

    markdown, _ = to_document(_PAGE_HTML, "https://ex.com/p")
    assert markdown.startswith("# "), markdown[:80]
    assert "## " in markdown, markdown[:200]
    assert markdown.count("\n\n") >= 3, markdown
