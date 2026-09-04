import json
from pathlib import Path

from score import _flatten, score

HERE = Path(__file__).parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())

_CASE = {"id": "c", "payload": "INJ-c", "visible": "VIS-c"}
_CONTROL = {**_CASE, "control": True}


def test_a_payload_split_by_zero_width_characters_still_counts_as_a_leak():
    smuggled = "\u200b".join("INJ-c")
    assert score(_CASE, f"VIS-c {smuggled}", False, None).contained is False


def test_a_payload_encoded_in_the_tag_block_still_counts_as_a_leak():
    smuggled = "".join(chr(0xE0000 + ord(ch)) for ch in "INJ-c")
    assert score(_CASE, f"VIS-c {smuggled}", False, None).contained is False


def test_returning_nothing_does_not_pass():
    result = score(_CASE, "", False, None)
    assert result.contained is True
    assert result.passed is False


def test_containing_the_payload_while_keeping_the_article_passes():
    assert score(_CASE, "VIS-c and the article text", False, None).passed is True


def test_an_error_never_passes():
    assert score(_CASE, "VIS-c", False, "boom").passed is False


def test_the_control_fails_when_a_fetcher_reports_a_page_with_nothing_hidden():
    assert score(_CONTROL, "VIS-c", True, None).passed is False


def test_the_control_passes_when_nothing_is_reported():
    assert score(_CONTROL, "VIS-c", False, None).passed is True


def test_every_manifest_case_has_a_corpus_file():
    for case in MANIFEST["cases"]:
        assert (HERE / "corpus" / case["file"]).is_file(), case["id"]


def test_every_corpus_file_is_in_the_manifest():
    listed = {case["file"] for case in MANIFEST["cases"]} | {"index.html"}
    assert {p.name for p in (HERE / "corpus").glob("*.html")} == listed


def _generated_index(tmp_path, monkeypatch) -> str:
    import build_corpus

    monkeypatch.setattr(build_corpus, "CORPUS", tmp_path)
    build_corpus._write_index(MANIFEST["cases"])
    return (tmp_path / "index.html").read_text()


def test_the_generated_index_lists_every_case_and_carries_no_payload(tmp_path, monkeypatch):
    index = _generated_index(tmp_path, monkeypatch)
    for case in MANIFEST["cases"]:
        assert case["file"] in index, case["id"]
        assert case["payload"] not in index, case["id"]


def test_the_committed_index_is_what_the_generator_produces(tmp_path, monkeypatch):
    assert _generated_index(tmp_path, monkeypatch) == (HERE / "corpus" / "index.html").read_text()


def test_every_case_plants_both_of_its_markers():
    for case in MANIFEST["cases"]:
        html = (HERE / "corpus" / case["file"]).read_text()
        assert case["visible"] in html, case["id"]
        if case.get("control"):
            continue
        assert case["payload"] in _flatten(html), case["id"]


def test_a_notice_about_the_report_does_not_count_as_disclosure():
    from adapters.groundhog import _FINDINGS

    assert "hidden_css" in _FINDINGS
    for notice in ("report_truncated", "strip_incomplete", "detection_degraded"):
        assert notice not in _FINDINGS, notice


def test_the_zero_width_case_hides_its_payload_rather_than_only_joining_it():
    html = (HERE / "corpus" / "evasion-zero-width.html").read_text()
    assert "display:none" in html


def test_the_publish_workflow_uploads_the_corpus_that_is_scored():
    workflow = (HERE.parent / ".github" / "workflows" / "pages.yml").read_text()
    assert "path: benchmark/corpus" in workflow


def test_a_hosted_fetcher_is_never_scored_against_a_loopback_corpus():
    run = (HERE / "run.py").read_text()
    gate = run[run.index("adapters = [") : run.index("rows = {")]
    for hosted in ("jina", "firecrawl"):
        assert f'"{hosted}"' in gate, hosted
    assert "if not local:" in gate


def test_firecrawl_is_measurable_without_a_key():
    from adapters import firecrawl

    assert firecrawl.available() is True


def test_firecrawl_sends_no_authorization_when_no_key_is_set(monkeypatch):
    from adapters import firecrawl

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert firecrawl.build_request("https://ex.com").has_header("Authorization") is False


def test_firecrawl_uses_the_key_when_one_is_set(monkeypatch):
    from adapters import firecrawl

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    assert firecrawl.build_request("https://ex.com").get_header("Authorization") == "Bearer fc-test"
