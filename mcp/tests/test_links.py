import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt

from groundhog_mcp import links

_FIXTURES = Path(__file__).parent / "fixtures"
_PAGE = "https://example.com/page"


def _found(markdown: str) -> list[tuple[str, str]]:
    return [(link.url, link.label) for link in links.find(markdown, _PAGE)]


def test_links_trafilatura_writes_are_read_with_their_labels() -> None:
    markdown = (_FIXTURES / "trafilatura_links.md").read_text()

    found = _found(markdown)

    assert ("https://example.com/spec/v2", "spec [v2]") in found
    assert ("https://other.org/path_(paper)", "a paper") in found
    assert ("https://other.org/#/paper", "hash route") in found
    assert ("https://x.org/a?utm_source=t&id=7", "tracked") in found
    assert ("https://example.com#ref1", "[1]") in found


def test_a_link_inside_inline_code_is_not_a_link() -> None:
    markdown = (_FIXTURES / "trafilatura_links.md").read_text()

    assert "https://evil.org/" not in [url for url, _ in _found(markdown)]


def test_a_link_in_a_fenced_block_is_not_a_link() -> None:
    assert _found("```\n[x](https://evil.org/)\n```\n") == []


def test_an_inline_code_span_hides_a_link_across_lines() -> None:
    assert _found("``a\n[x](https://evil.org/) b``") == []


def test_an_image_is_not_a_link_but_a_linked_image_is() -> None:
    found = _found(
        "![pic](https://img.org/i.png) and [![logo](https://img.org/l.png)](https://img.org/)"
    )

    assert found == [("https://img.org/", "logo")]


def test_each_link_records_the_exact_slice_it_came_from() -> None:
    markdown = "Read [the spec](https://a.org/s) now."

    (link,) = links.find(markdown, _PAGE)

    assert markdown[link.start : link.end] == "[the spec](https://a.org/s)"


def test_only_http_links_are_kept() -> None:
    assert _found("[m](mailto:a@b.org) [j](javascript:alert(1)) [f](ftp://a.org/)") == []


def test_a_link_with_a_title_keeps_only_its_destination() -> None:
    assert _found('[s](https://a.org/s "The spec")') == [("https://a.org/s", "s")]


def test_identity_drops_what_never_names_another_page() -> None:
    assert links.identity("HTTPS://Example.COM:443/Path?utm_source=x&id=2&fbclid=y#top") == (
        "https://example.com/Path?id=2"
    )


def test_identity_keeps_a_meaningful_query_and_the_path_case() -> None:
    assert links.identity("https://a.org/Doc?page=2") != links.identity("https://a.org/doc?page=2")
    assert links.identity("https://a.org/doc?page=2") != links.identity("https://a.org/doc?page=3")


def test_a_fragment_on_this_page_or_its_site_root_is_the_same_page() -> None:
    assert links.same_page("https://example.com/page#ref1", _PAGE)
    assert links.same_page("https://example.com#ref1", _PAGE)
    assert not links.same_page("https://other.org/#/paper", _PAGE)
    assert not links.same_page("https://example.com/other#a", _PAGE)
    assert not links.same_page("https://example.com/page", _PAGE)


def test_pages_that_differ_only_by_numbers_share_a_pattern() -> None:
    assert links.pattern("https://a.org/2026/10/23?page=4") == links.pattern(
        "https://a.org/2025/01/02?page=9"
    )
    assert links.pattern("https://a.org/a") != links.pattern("https://a.org/b")


def _commonmark_links(markdown: str) -> list[str]:
    parser = MarkdownIt("commonmark")
    hrefs: list[str] = []
    for token in parser.parse(markdown):
        for child in token.children or []:
            if child.type == "link_open":
                hrefs.append(str(child.attrs["href"]))
    return [href for href in hrefs if href.startswith(("http://", "https://"))]


@pytest.mark.parametrize("name", ["trafilatura_links.md", "long_paragraph_page.md"])
def test_the_links_found_are_the_ones_commonmark_finds(name: str) -> None:
    markdown = (_FIXTURES / name).read_text()

    found = [link.url for link in links.find(markdown, _PAGE)]

    assert [unquote(url) for url in found] == [
        unquote(href) for href in _commonmark_links(markdown)
    ]


def test_the_captured_pages_agree_with_commonmark_too() -> None:
    for doc in json.loads((_FIXTURES / "documents.json").read_text()).values():
        found = [link.url for link in links.find(doc["markdown"], doc["final_url"])]
        assert [unquote(url) for url in found] == [
            unquote(href) for href in _commonmark_links(doc["markdown"])
        ]
