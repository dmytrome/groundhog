import json
import random
import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt

from groundhog_mcp import extract, links

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


def test_a_link_to_a_section_of_the_home_page_is_another_page() -> None:
    assert not links.same_page("https://example.com/#pricing", _PAGE)


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


def test_what_the_installed_extractor_writes_is_read_as_commonmark_reads_it() -> None:
    html = (_FIXTURES / "trafilatura_links.html").read_text()
    markdown, _ = extract.to_document(html, _PAGE)

    found = [link.url for link in links.find(markdown, _PAGE)]

    assert "https://example.com/spec/v2" in found
    assert [unquote(url) for url in found] == [
        unquote(href) for href in _commonmark_links(markdown)
    ]


@pytest.mark.parametrize(
    "markdown",
    [
        "`open\n```\nclose` [evil](https://evil.org/x)\n```\n",
        "Text.\n\n```\n[evil](https://evil.org/x)\n",
        "Text.\n\n    [evil](https://evil.org/x)\n",
        "- item\n\n      [evil](https://evil.org/x)\n",
        '<span title="[evil](https://evil.org/x)">a</span>',
    ],
)
def test_a_link_inside_code_or_markup_is_not_offered(markdown: str) -> None:
    assert _found(markdown) == []
    assert _commonmark_links(markdown) == []


@pytest.mark.parametrize(
    ("markdown", "url"),
    [
        ("See <https://auto.org/q>.", "https://auto.org/q"),
        ("See [the spec][s].\n\n[s]: https://ref.org/s", "https://ref.org/s"),
        ("See [S][].\n\n[s]: https://ref.org/s", "https://ref.org/s"),
        ("See [s].\n\n[S]: https://ref.org/s", "https://ref.org/s"),
        ("See [a](https://w.org/A_(b_(c))).", "https://w.org/A_(b_(c))"),
    ],
)
def test_every_kind_of_commonmark_link_is_found(markdown: str, url: str) -> None:
    assert [link.url for link in links.find(markdown, _PAGE)] == [url]
    assert _commonmark_links(markdown) == [url]


def test_a_url_with_a_port_that_is_not_a_number_is_not_offered() -> None:
    assert _found("[b](https://a.org:bad/x)") == []


@pytest.mark.parametrize(
    "markdown",
    [
        "[" * 250_000,
        "[](a" * 250_000,
        "`a" * 250_000,
        '[a](b "' * 250_000,
        "[](" + "(a" * 250_000,
    ],
)
def test_a_hostile_page_is_read_in_linear_time(markdown: str) -> None:
    started = time.perf_counter()
    links.find(markdown, _PAGE)
    assert time.perf_counter() - started < 5


_PIECES = [
    "[a](https://a.org/x)",
    "[b c](<https://b.org/y z>)",
    '[t](https://t.org/ "title")',
    "![i](https://i.org/p.png)",
    "[![logo](https://i.org/l.png)](https://img.org/)",
    "<https://auto.org/q>",
    "[ref]",
    "[x][ref]",
    "[ref][]",
    "`code [c](https://c.org/)`",
    "[l](https://l.org/a_(b))",
    "word",
    " ",
    "\n",
    "\n\n",
    "\n\n    [i](https://indented.org/)\n\n",
    "\n\n```\n[f](https://fenced.org/)\n```\n\n",
    "\n> ",
    "\n- ",
    "\n# ",
    "*",
    "_",
]


def test_well_formed_markdown_is_read_as_commonmark_reads_it() -> None:
    rng = random.Random(20261023)
    for _ in range(3000):
        markdown = "".join(rng.choice(_PIECES) for _ in range(rng.randint(1, 20)))
        if rng.random() < 0.5:
            markdown += "\n\n[ref]: https://ref.org/r\n"
        assert [unquote(link.url) for link in links.find(markdown, _PAGE)] == [
            unquote(url) for url in _commonmark_links(markdown)
        ], markdown


def test_a_link_wrapped_across_windows_line_endings_is_found() -> None:
    markdown = "See [the\r\nspec](https://x.org/a) now.\r\n\r\n[s]:\r\n  https://x.org/b\r\n\r\n[s]"

    found = links.find(markdown, _PAGE)

    assert [(link.url, link.label) for link in found] == [
        ("https://x.org/a", "the spec"),
        ("https://x.org/b", "s"),
    ]
    assert markdown[found[0].start : found[0].end] == "[the\r\nspec](https://x.org/a)"


def test_untracked_drops_only_tracking_ids_and_keeps_the_rest_as_written() -> None:
    assert links.untracked("https://a.org/P?utm_source=x&id=2&fbclid=y&q=a+b#top") == (
        "https://a.org/P?id=2&q=a+b#top"
    )
    assert links.untracked("https://a.org/p") == "https://a.org/p"


def test_a_code_span_opened_before_the_window_is_still_read_as_code() -> None:
    markdown = "`" + "x" * 5000 + "` [real](https://a.org/r) `code [fake](https://b.org/f)`"
    passage = (len(markdown) - 60, len(markdown))

    found = [link.url for link in links.find(markdown, _PAGE, within=[passage])]

    assert found == ["https://a.org/r"]


def test_finding_links_near_one_passage_does_not_read_the_whole_page() -> None:
    markdown = "Cats [nap](https://a.org/n).\n\n" + "[b](https://b.org/x) " * 100_000
    passage = (0, 10)

    started = time.perf_counter()
    near = links.find(markdown, _PAGE, within=[passage])
    took = time.perf_counter() - started
    started = time.perf_counter()
    links.find(markdown, _PAGE)
    whole = time.perf_counter() - started

    assert [link.url for link in near] == ["https://a.org/n"]
    assert took < whole / 4
