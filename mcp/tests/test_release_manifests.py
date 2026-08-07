import json
import re
import tomllib
from pathlib import Path

import pytest

# Each of these manifests is consumed by a different registry (MCP Registry, Gemini CLI,
# LobeHub) and none of them is exercised by anything else in the suite, so a forgotten
# bump stays invisible until a user is served a stale version. 0.10.1 shipped with
# gemini-extension.json still on 0.10.0 for exactly that reason. `release.yml` runs
# pytest before it publishes, which makes this the point where the drift can still be
# caught.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_VERSION_FIELDS: tuple[tuple[str, tuple[str | int, ...]], ...] = (
    ("server.json", ("version",)),
    ("server.json", ("packages", 0, "version")),
    ("gemini-extension.json", ("version",)),
    ("lhm.plugin.json", ("version",)),
    (".cursor-plugin/plugin.json", ("version",)),
)


def _package_version() -> str:
    with (_REPO_ROOT / "mcp" / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    assert isinstance(version, str)
    return version


def _dig(data: object, keys: tuple[str | int, ...]) -> object:
    for key in keys:
        assert isinstance(data, (dict, list)), f"{keys} does not address {type(data)}"
        data = data[key]  # type: ignore[index]
    return data


@pytest.mark.parametrize(("manifest", "keys"), _VERSION_FIELDS)
def test_manifest_version_matches_the_package(manifest: str, keys: tuple[str | int, ...]) -> None:
    declared = _dig(json.loads((_REPO_ROOT / manifest).read_text()), keys)
    assert declared == _package_version(), f"{manifest}{list(keys)} is stale"


def test_the_pinned_browser_image_matches_the_package_version() -> None:
    # publish-image.yml tags the image `{{version}}` off the `v*` release tag, so the
    # image the MCP image builds on only exists once this release is out.
    dockerfile = (_REPO_ROOT / "mcp" / "Dockerfile").read_text()
    pin = re.search(r"^ARG BROWSER_IMAGE=\S+:(\S+)$", dockerfile, re.MULTILINE)
    assert pin is not None, "no ARG BROWSER_IMAGE pin in mcp/Dockerfile"
    assert pin.group(1) == _package_version()


def test_the_lockfile_records_the_package_version() -> None:
    # `uv sync` regenerates this, but it is committed, so a bump made without running uv
    # leaves the one version site the JSON checks above cannot see.
    lock = tomllib.loads((_REPO_ROOT / "mcp" / "uv.lock").read_text())
    ours = [pkg for pkg in lock["package"] if pkg["name"] == "groundhog-mcp"]
    assert len(ours) == 1, "groundhog-mcp is not in uv.lock exactly once"
    assert ours[0]["version"] == _package_version()


def _changelog() -> tuple[set[str], set[str]]:
    text = (_REPO_ROOT / "CHANGELOG.md").read_text()
    headings = set(re.findall(r"^## \[([^\]]+)\]", text, re.MULTILINE))
    defined = set(re.findall(r"^\[([^\]]+)\]: \S+", text, re.MULTILINE))
    return headings, defined


def test_the_released_version_has_a_changelog_entry() -> None:
    # Without this the guard is satisfied by a release that bumps every manifest and
    # documents nothing, which is the same drift one step further along.
    headings, _ = _changelog()
    assert _package_version() in headings


def test_every_changelog_version_heading_resolves_to_a_release_link() -> None:
    # The headings are shortcut reference links; without a matching definition GitHub
    # renders a literal `[0.10.1]`. 0.10.0 and 0.10.1 both shipped without one.
    headings, defined = _changelog()
    # Keep a Changelog's `## [Unreleased]` names no tag and never gets a release link.
    headings.discard("Unreleased")
    assert headings <= defined, f"undefined changelog links: {sorted(headings - defined)}"
