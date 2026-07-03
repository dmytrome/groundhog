from groundhog_mcp.retrieval import select

DOC = """# Intro

Cats are small carnivorous mammals kept as pets around the world.

## Diet

Dogs are loyal domesticated animals often trained for work and companionship.

## Feline behavior

The cat is a crepuscular hunter that stalks prey using acute night vision.
"""


def test_returns_only_relevant_passages_in_document_order():
    body, matches, truncated = select(DOC, "cat hunting vision", max_tokens=10000)
    assert "crepuscular hunter" in body
    assert "loyal domesticated" not in body  # dog paragraph is irrelevant
    # matches are ordered by document offset, not by score
    assert [m["offset"] for m in matches] == sorted(m["offset"] for m in matches)
    assert truncated is False


def test_match_carries_nearest_heading():
    _, matches, _ = select(DOC, "crepuscular night vision", max_tokens=10000)
    assert matches[0]["heading"] == "Feline behavior"


def test_no_match_returns_empty_for_caller_fallback():
    body, matches, truncated = select(DOC, "quantum chromodynamics lattice", max_tokens=10000)
    assert body == ""
    assert matches == []
    assert truncated is False


def test_budget_drops_lowest_ranked_and_marks_truncated():
    body, matches, truncated = select(DOC, "cat dog animals", max_tokens=10)  # ~40 chars
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
    body, matches, _ = select(doc, "loyal domesticated companionship", max_tokens=10000)
    assert "Loyal domesticated" in body
    assert matches and matches[0]["heading"] == "Dogs"
