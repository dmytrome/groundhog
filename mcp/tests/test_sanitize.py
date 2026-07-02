from groundhog_mcp.sanitize import strip_invisible


def test_clean_text_no_threats():
    out, threats = strip_invisible("plain visible text")
    assert out == "plain visible text"
    assert threats == []


def test_zero_width_stripped_and_reported():
    out, threats = strip_invisible("he​llo​world")
    assert out == "helloworld"
    assert len(threats) == 1
    assert threats[0]["type"] == "zero_width"
    assert threats[0]["reason"] == "U+200B x2"


def test_bidi_override_reported_as_bidi():
    out, threats = strip_invisible("safe‮txet")
    assert "‮" not in out
    assert threats[0]["type"] == "bidi"
    assert threats[0]["reason"] == "U+202E x1"


def test_strip_false_keeps_chars_but_reports():
    out, threats = strip_invisible("a​b", strip=False)
    assert out == "a​b"
    assert len(threats) == 1
