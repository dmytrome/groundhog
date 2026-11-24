import hashlib
import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from mcp.client.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, Request, RequestParams, Result
from pydantic import ConfigDict

from groundhog_mcp import server, skills

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None

_SKILLS_DIR = Path(skills.__file__).parent / "skills"
_SKILL_URI = "skill://groundhog-research/SKILL.md"
_TS_CLIENT = Path(__file__).parent / "fixtures" / "skills_ts_client.json"
_DEMO = "---\nname: demo\ndescription: d\n---\n\n# Demo\n"


class _Open(Result):
    model_config = ConfigDict(extra="allow")


class _Get(RequestParams):
    uri: str


async def _call(client: Client, method: str, params: RequestParams | None = None) -> Json:
    result = await client.session.send_request(Request(method=method, params=params), _Open)
    return result.model_dump(by_alias=True, exclude_none=True)


def _skill(root: Path, name: str, text: str = _DEMO) -> Path:
    (root / name).mkdir()
    (root / name / "SKILL.md").write_text(text.replace("name: demo", f"name: {name}"))
    return root / name


async def _served(extension: skills.Skills) -> tuple[list[str], set[str]]:
    async with Client(MCPServer("t", extensions=[extension])) as client:
        listing = await _call(client, "skills/list")
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
    return [entry["uri"] for entry in listing["skills"]], resources


async def test_the_server_declares_the_skills_extension_and_resources() -> None:
    async with Client(server.build_server()) as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_capabilities.extensions == {skills.EXTENSION_ID: {}}
        assert client.server_capabilities.resources is not None


async def test_the_listing_is_a_complete_manifest_of_what_the_server_serves() -> None:
    async with Client(server.build_server()) as client:
        listing = await _call(client, "skills/list")
        listed = {r.uri for r in (await client.list_resources()).resources}
        assert listing["resultType"] == "complete"
        assert (listing["ttlMs"], listing["cacheScope"]) == (300_000, "public")
        [entry] = listing["skills"]
        assert entry["uri"] == _SKILL_URI
        assert _SKILL_URI in {item["uri"] for item in entry["resources"]}
        for item in entry["resources"]:
            assert item["uri"] in listed
            [content] = (await client.read_resource(item["uri"])).contents
            raw = content.text.encode()
            assert item["size"] == len(raw)
            assert item["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


async def test_the_skill_file_is_served_with_its_frontmatter() -> None:
    raw = (_SKILLS_DIR / "groundhog-research" / "SKILL.md").read_text()
    written = yaml.safe_load(raw.split("---\n")[1])

    async with Client(server.build_server()) as client:
        [entry] = (await _call(client, "skills/list"))["skills"]
        [resource] = [
            r for r in (await client.list_resources()).resources if str(r.uri) == _SKILL_URI
        ]

    assert entry["frontmatter"] == written
    assert written["name"] == _SKILL_URI.split("/")[-2]
    assert (resource.name, resource.description) == (written["name"], written["description"])
    assert resource.mime_type == "text/markdown"


async def test_getting_a_skill_returns_its_listing_entry() -> None:
    async with Client(server.build_server()) as client:
        [entry] = (await _call(client, "skills/list"))["skills"]
        got = await _call(client, "skills/get", _Get(uri=_SKILL_URI))

    assert got["skill"] == entry
    assert (got["resultType"], got["ttlMs"], got["cacheScope"]) == ("complete", 300_000, "public")


async def test_an_unknown_skill_is_invalid_params() -> None:
    async with Client(server.build_server()) as client:
        with pytest.raises(MCPError) as caught:
            await _call(client, "skills/get", _Get(uri="skill://nope/SKILL.md"))

    assert caught.value.error.code == INVALID_PARAMS


async def test_the_instructions_point_at_the_skill() -> None:
    async with Client(server.build_server()) as client:
        assert _SKILL_URI in (client.instructions or "")


async def test_a_legacy_client_keeps_the_tools_without_the_extension() -> None:
    async with Client(server.build_server(), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        assert not client.server_capabilities.extensions
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {"read_url", "research", "search", "status"} <= names


def test_every_packaged_skill_parses_the_same_as_yaml() -> None:
    for skill_md in _SKILLS_DIR.glob("*/SKILL.md"):
        text = skill_md.read_text()
        assert skills.frontmatter(text) == yaml.safe_load(text.split("---\n")[1])


def test_windows_line_endings_and_a_byte_order_mark_parse_like_the_plain_file() -> None:
    text = (_SKILLS_DIR / "groundhog-research" / "SKILL.md").read_text()

    windows = "﻿" + text.replace("\n", "\r\n")

    assert skills.frontmatter(windows) == skills.frontmatter(text)


@pytest.mark.parametrize(
    "head",
    [
        "name: a\ndescription: b\n",
        "---\nname: a\ndescription: b\n",
        "---\nname: a\n---\n",
        "---\nname: a\nname: b\ndescription: c\n---\n",
        "---\nname: a\ndescription: 'quoted'\n---\n",
        "---\nname: a\ndescription: has: colon\n---\n",
        "---\nname: a\ndescription: trailing # comment\n---\n",
        "---\nname: a\ndescription: >\n  folded\n---\n",
        "---\nname: a\nmetadata:\n  k: v\ndescription: b\n---\n",
        "---\nname: a\ndescription: " + "x" * 1025 + "\n---\n",
        "---\nname: " + "a" * 65 + "\ndescription: b\n---\n",
    ],
)
def test_frontmatter_outside_the_flat_plain_contract_is_refused(head: str) -> None:
    with pytest.raises(ValueError):
        skills.frontmatter(head + "\n# body\n")


async def test_a_broken_skill_is_skipped_and_the_others_are_still_served(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _skill(tmp_path, "good")
    _skill(tmp_path, "wrong").joinpath("SKILL.md").write_text(_DEMO)
    (_skill(tmp_path, "binary") / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff")
    (tmp_path / "empty").mkdir()

    with caplog.at_level(logging.WARNING, logger="groundhog_mcp.skills"):
        listed, resources = await _served(skills.Skills(tmp_path))

    assert listed == ["skill://good/SKILL.md"]
    assert resources == {"skill://good/SKILL.md"}
    skipped = " ".join(record.getMessage() for record in caplog.records)
    for name in ("binary", "empty", "wrong"):
        assert f"skipping skill {name}:" in skipped


async def test_hidden_files_and_folders_are_not_served(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "demo")
    (skill / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "x.pyc").write_bytes(b"\xff")
    (tmp_path / ".cache").mkdir()

    async with Client(MCPServer("t", extensions=[skills.Skills(tmp_path)])) as client:
        [entry] = (await _call(client, "skills/list"))["skills"]
        uris = {str(r.uri) for r in (await client.list_resources()).resources}

    assert [item["uri"] for item in entry["resources"]] == ["skill://demo/SKILL.md"]
    assert uris == {"skill://demo/SKILL.md"}


async def test_supporting_files_join_the_manifest(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "demo")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("guide\n")

    async with Client(MCPServer("t", extensions=[skills.Skills(tmp_path)])) as client:
        [entry] = (await _call(client, "skills/list"))["skills"]
        [guide] = (await client.read_resource("skill://demo/references/guide.md")).contents

    assert [item["uri"] for item in entry["resources"]] == [
        "skill://demo/SKILL.md",
        "skill://demo/references/guide.md",
    ]
    assert guide.text == "guide\n"


def test_requests_captured_from_the_typescript_skills_client_get_the_same_answers() -> None:
    pairs = json.loads(_TS_CLIENT.read_text())
    served = subprocess.Popen(
        [sys.executable, "-m", "groundhog_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert served.stdin is not None and served.stdout is not None
    watchdog = threading.Timer(60, served.kill)
    watchdog.start()

    answers: dict[int, Json] = {}
    try:
        for pair in pairs:
            served.stdin.write(json.dumps(pair["request"]) + "\n")
            served.stdin.flush()
            reply = json.loads(served.stdout.readline())
            answers[reply["id"]] = reply
    finally:
        watchdog.cancel()
        served.kill()
        served.wait()

    for pair in pairs:
        want, got = pair["response"], answers[pair["request"]["id"]]
        if "error" in want:
            assert got["error"]["code"] == want["error"]["code"]
            continue
        result = {key: value for key, value in got["result"].items() if key != "_meta"}
        assert result == want["result"], "refresh the fixture after changing a packaged skill"
