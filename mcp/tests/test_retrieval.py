import random
import time

from markdown_it import MarkdownIt

from groundhog_mcp import extract
from groundhog_mcp.retrieval import (
    _HEADING_RE,
    Chunk,
    _fenced_lines,
    chunk_document,
    rank,
    select,
)

DOC = """# Intro

Cats are small carnivorous mammals kept as pets around the world.

## Diet

Dogs are loyal domesticated animals often trained for work and companionship.

## Feline behavior

The cat is a crepuscular hunter that stalks prey using acute night vision.
"""


def test_returns_only_relevant_passages_in_document_order():
    body, matches, truncated, _ = select(DOC, "cat hunting vision", max_tokens=10000)
    assert "crepuscular hunter" in body
    assert "loyal domesticated" not in body  # dog paragraph is irrelevant
    # matches are ordered by document offset, not by score
    assert [m["offset"] for m in matches] == sorted(m["offset"] for m in matches)
    assert truncated is False


def test_match_carries_nearest_heading():
    _, matches, _, _ = select(DOC, "crepuscular night vision", max_tokens=10000)
    assert matches[0]["heading"] == "Feline behavior"


def test_no_match_returns_empty_for_caller_fallback():
    body, matches, truncated, _ = select(DOC, "quantum chromodynamics lattice", max_tokens=10000)
    assert body == ""
    assert matches == []
    assert truncated is False


def test_budget_drops_lowest_ranked_and_marks_truncated():
    body, matches, truncated, _ = select(DOC, "cat dog animals", max_tokens=10)  # ~40 chars
    assert truncated is True
    assert len(matches) >= 1


def test_ranking_is_deterministic():
    a = select(DOC, "cat hunter vision", max_tokens=10000)
    b = select(DOC, "cat hunter vision", max_tokens=10000)
    assert a == b


def test_body_without_blank_line_after_heading_is_searchable():
    # A heading directly followed by its body (no blank line) must still yield a
    # searchable body chunk, not be swallowed whole into the heading.
    doc = "# Dogs\nLoyal domesticated animals trained for companionship and work."
    body, matches, _, _ = select(doc, "loyal domesticated companionship", max_tokens=10000)
    assert "Loyal domesticated" in body
    assert matches and matches[0]["heading"] == "Dogs"


def test_heading_is_capped():
    # Headings ride along in every match/passage and never pass through the
    # token budget, so an enormous one would flood context outside max_tokens.
    chunks = chunk_document("# " + "h" * 500 + "\n\nbody text here\n")
    assert len(chunks[0].heading) == 200


_ABOUT_CATS = "\n\n".join(
    [
        "## Feeding",
        "A cat eats meat and a kitten eats more often than a grown cat does each day.",
        "## Litter",
        "A cat wants its litter tray cleaned each day, which no cat owner enjoys.",
        "## Sleeping",
        "A cat sleeps most of the day, and a kitten sleeps more of the day than that.",
        "## Grooming",
        "A cat grooms itself each day, and a kitten learns the habit from its mother.",
        "## Company",
        "A cat kept alone each day fares worse than a cat that has another cat about.",
    ]
)


def test_two_different_questions_do_not_return_the_same_passages():
    feeding, _, _, _ = select(_ABOUT_CATS, "what does a kitten eat", 20000)
    litter, _, _, _ = select(_ABOUT_CATS, "how often is the litter tray cleaned", 20000)
    assert feeding != litter
    assert "eats meat" in feeding and "litter tray" not in feeding
    assert "litter tray" in litter and "eats meat" not in litter


def test_a_question_does_not_bring_back_the_whole_document():
    body, _, _, _ = select(_ABOUT_CATS, "what does a kitten eat", 20000)
    assert len(body) < len(_ABOUT_CATS) / 2


def test_narrowing_to_the_question_is_not_reported_as_a_budget_truncation():
    _, _, truncated, _ = select(_ABOUT_CATS, "what does a kitten eat", 20000)
    assert truncated is False


def test_the_count_of_passages_the_floor_set_aside_is_reported():
    _, matches, truncated, set_aside = select(_ABOUT_CATS, "what does a kitten eat", 20000)
    scoring, _, _ = rank(chunk_document(_ABOUT_CATS), "what does a kitten eat", 20000)
    assert truncated is False
    assert set_aside == len(scoring) - len(matches) > 0


def test_a_pooled_ranking_keeps_a_source_that_corroborates_rather_than_answers():
    chunks = [
        Chunk(heading=None, offset=0, source=name, text=text)
        for name, text in (
            ("a", "Cats eat meat and a kitten eats more often than a grown cat does."),
            ("b", "A kitten needs feeding several times a day."),
            ("c", "Feeding a cat once a day suits an adult animal."),
        )
    ]
    ranked, _, _ = rank(chunks, "what does a kitten eat", 20000)
    assert [s.chunk.source for s in ranked] == ["a", "b", "c"]


def test_a_short_page_is_narrowed_like_any_other():
    markdown = "\n\n".join(
        [
            "## A",
            "kitten eats meat daily and a kitten eats it twice",
            "## B",
            "the kitten tray is emptied twice daily",
            "## C",
            "vaccinations are due at twelve weeks",
        ]
    )
    _, matches, _, _ = select(markdown, "what does a kitten eat", 20000)
    assert [m["heading"] for m in matches] == ["A"]


def test_a_one_word_chunk_does_not_set_the_bar_for_the_paragraph_that_answers():
    filler = "\n\n".join(
        f"Paragraph {i} talks about cats and their habits at some length without the key word. " * 3
        for i in range(6)
    )
    paragraph = "To install the tool, run the installer and follow the getting started guide. " + (
        "After that the daemon starts on its own and the browser image is pulled on first use. " * 5
    )
    markdown = "# Page\n\nInstall\n\n" + filler + "\n\n" + paragraph + "\n"
    body, matches, _, set_aside = select(markdown, "install", 20000)
    assert "run the installer" in body
    assert len(matches) == 2 and set_aside == 0


def test_a_passage_matching_every_term_the_best_one_matches_is_not_set_aside_for_length():
    filler = "\n\n".join(
        f"Paragraph {i} talks about cats and their habits at some length without the key word. " * 3
        for i in range(6)
    )
    nav = "Install guide: how to set up the tool quickly on any machine today"
    paragraph = "To install the tool, read the guide, run the installer and check the version. " + (
        "After that the daemon starts on its own and the browser image is pulled on first use. " * 5
    )
    markdown = "# Page\n\n" + nav + "\n\n" + filler + "\n\n" + paragraph + "\n"
    body, _, _, set_aside = select(markdown, "install guide", 20000)
    assert "run the installer" in body and set_aside == 0


def test_two_sections_that_both_answer_the_question_both_survive():
    markdown = "\n\n".join(
        [
            "## Kitten feeding",
            "A kitten eats meat several times a day while it is still growing fast.",
            "## Kitten meals",
            "A kitten eats meat in small meals rather than one large meal each day.",
            "## Litter",
            "The tray wants cleaning daily, which is the part that nobody enjoys.",
        ]
    )
    body, matches, _, _ = select(markdown, "what does a kitten eat", 20000)
    assert len(matches) == 2
    assert "several times a day" in body and "small meals" in body


def test_a_chunk_that_opens_with_a_fence_reports_where_it_starts():
    markdown = "para one here\n\n```bash\ncode line\n```\n\n## Heading\n\nbody text\n"
    assert [c.offset for c in chunk_document(markdown)] == [0, 15, 50]


def test_a_fence_that_is_never_closed_does_not_swallow_the_headings_after_it():
    markdown = "intro text\n\n```\ncode\n\n## Real Heading\n\nreal body\n"
    assert "Real Heading" in [c.heading for c in chunk_document(markdown)]


def test_a_stray_fence_elsewhere_does_not_expose_the_block_that_is_closed():
    markdown = "## Install\n\n```\n# or from a checkout\ndocker compose up\n```\n\n```\n"
    assert "or from a checkout" not in [c.heading for c in chunk_document(markdown)]


def test_a_closed_block_keeps_its_cover_when_a_later_one_is_left_open():
    markdown = (
        "## Install\n\n```\n# or from a checkout\ndocker compose up\n```\n\n"
        "## Logs\n\n```\n# tail the log\ndocker logs\n"
    )
    headings = [c.heading for c in chunk_document(markdown)]
    assert "or from a checkout" not in headings
    assert "Install" in headings and "Logs" in headings


def test_fence_lines_pair_in_the_order_they_appear():
    markdown = "## A\n\n```\ncode\n\n## B\n\n```\nmore code\n```\n\n## C\n\ntail\n"
    assert [c.heading for c in chunk_document(markdown)] == ["A", "C"]


def test_a_shorter_run_of_backticks_does_not_close_a_longer_one():
    markdown = "## Example\n\n````\n```\n# a shell comment\n```\n````\n\ntail text\n"
    assert "a shell comment" not in [c.heading for c in chunk_document(markdown)]


def test_a_fence_is_closed_only_by_the_marker_that_opened_it():
    markdown = "```\ncode\n~~~\n## Inside\n~~~\nmore\n```\n\n## After\n\nbody\n"
    headings = [c.heading for c in chunk_document(markdown)]
    assert "Inside" not in headings
    assert "After" in headings


def test_a_paragraph_opening_with_an_inline_code_span_is_not_a_fence():
    markdown = "## Backticks\n\n``` ``double`` ``` is awkward.\n\n## Install\n\nbody\n"
    assert [c.heading for c in chunk_document(markdown)] == ["Backticks", "Install"]


def test_openers_that_can_never_close_are_not_each_scanned_to_the_end():
    lines: list[str] = []
    for width in range(6000, 2, -1):
        lines += ["~" * width, "prose about cats"]
    start = time.perf_counter()
    assert _fenced_lines(lines) == set()
    assert time.perf_counter() - start < 0.5


def test_a_shell_comment_inside_a_fence_is_not_a_heading():
    markdown = "## Install\n\n```bash\n# or from a checkout\ndocker compose up\n```\n"
    chunks = chunk_document(markdown)
    assert [c.heading for c in chunks] == ["Install"]
    assert "# or from a checkout" in chunks[0].text


_FRAGMENTS = [
    "## Install",
    "### Notes on `--flag` and `--other`",
    "`inline` opens a sentence that keeps going.",
    "``` ``double`` ``` is an awkward way to write it.",
    "```",
    "```sh",
    "````",
    "~~~",
    "~~~~",
    "```python",
    "# a shell comment run --now",
    "# Heading swallowed by a block",
    "cmd --do-the-thing",
    "",
    "Ordinary prose about kittens and their diet.",
    "   ```",
    "```` ```js ````",
]


def _headings_markdown_it_finds(parser: MarkdownIt, text: str) -> set[int]:
    return {
        token.map[0]
        for token in parser.parse(text)
        if token.type == "heading_open" and token.markup.startswith("#")
    }


def _headings_we_find(text: str) -> set[int]:
    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    return {i for i, line in enumerate(lines) if i not in fenced and _HEADING_RE.match(line)}


def _disagreements(documents: list[str]) -> list[tuple[str, list[int], list[int]]]:
    parser = MarkdownIt("commonmark")
    out: list[tuple[str, list[int], list[int]]] = []
    for text in documents:
        theirs = _headings_markdown_it_finds(parser, text)
        mine = _headings_we_find(text)
        if mine != theirs:
            out.append((text, sorted(theirs - mine), sorted(mine - theirs)))
    return out


def test_a_fence_opened_inside_a_list_item_is_paired_on_flat_lines():
    markdown = "- list item\n   ~~~\n# a shell comment\n~~~~\n"
    assert _headings_markdown_it_finds(MarkdownIt("commonmark"), markdown) == {2}
    assert _headings_we_find(markdown) == set()


def test_no_heading_commonmark_finds_is_read_as_code():
    rng = random.Random(20260915)
    documents = [
        "\n".join(rng.choice(_FRAGMENTS) for _ in range(rng.randrange(4, 30))) + "\n"
        for _ in range(400)
    ]
    hidden = [(text, lost) for text, lost, _ in _disagreements(documents) if lost]
    assert not hidden[:3]


def test_a_document_whose_fences_close_is_chunked_as_commonmark_reads_it():
    rng = random.Random(20260915)
    documents = []
    for _ in range(400):
        body = [rng.choice(_FRAGMENTS) for _ in range(rng.randrange(4, 30))]
        documents.append("\n".join(body + ["```", "~~~", "~~~~", "````"]) + "\n")
    assert not _disagreements(documents)[:3]


_PAGE_WITH_A_TILDE_LINE = """<html><body><article>
<h2>Intro</h2>
<p>Some prose about the thing we are describing here at length for the extractor.</p>
<p>~~~</p>
<h2>Install</h2>
<p>Another paragraph of ordinary prose so the extractor keeps this section around.</p>
<pre><code>cmd --run
# a shell comment
</code></pre>
<h2>Notes</h2>
<p>A closing paragraph with enough words in it to survive the extraction heuristics.</p>
</article></body></html>"""


def test_a_fence_line_in_a_real_page_body_costs_no_heading_after_it():
    markdown, _ = extract.to_document(_PAGE_WITH_A_TILDE_LINE, url="https://example.com/x")
    assert "\n~~~\n" in markdown
    headings = [c.heading for c in chunk_document(markdown)]
    assert set(headings) == {"Intro", "Install", "Notes"}
