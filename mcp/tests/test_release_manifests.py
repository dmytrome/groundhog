import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

# Each of these manifests is consumed by a different registry — one entry per listing, so
# add a line here whenever a new one is added — and none is exercised by anything else in
# the suite, so a forgotten bump stays invisible until a user is served a stale version.
# 0.10.1 shipped with
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


_BUILD_COMMAND = "uv build --build-constraint build-constraints.txt"
_VALIDATE_COMMAND = "twine check --strict dist/*"


def _workflow(name: str) -> dict:
    return yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / name).read_text())


def _runs(job: dict) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def _upstream_of(workflow: dict, job: str) -> set[str]:
    seen: set[str] = set()
    queue = [job]
    while queue:
        needs = workflow["jobs"][queue.pop()].get("needs", [])
        for dep in [needs] if isinstance(needs, str) else needs:
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return seen


def test_the_build_backend_is_pinned_to_an_exact_version() -> None:
    constraints = (_REPO_ROOT / "mcp" / "build-constraints.txt").read_text()
    assert re.search(r"^hatchling==\d+\.\d+\.\d+$", constraints, re.MULTILINE), constraints


@pytest.mark.parametrize("workflow", ("ci.yml", "release.yml"))
def test_every_workflow_builds_against_the_pinned_backend(workflow: str) -> None:
    assert any(_BUILD_COMMAND in cmd for job in _workflow(workflow)["jobs"].values()
               for cmd in _runs(job)), workflow


@pytest.mark.parametrize("workflow", ("ci.yml", "release.yml"))
def test_every_workflow_validates_the_built_distribution(workflow: str) -> None:
    assert any(_VALIDATE_COMMAND in cmd for job in _workflow(workflow)["jobs"].values()
               for cmd in _runs(job)), workflow


def test_no_release_job_can_publish_without_an_earlier_job_having_run_the_gate() -> None:
    workflow = _workflow("release.yml")
    gates = {name for name, job in workflow["jobs"].items()
             if any(_VALIDATE_COMMAND in cmd for cmd in _runs(job))}
    assert gates, "no job runs the distribution gate"
    publishers = [name for name in workflow["jobs"] if name.startswith("publish")]
    assert publishers, "no publishing job found"
    for publisher in publishers:
        assert gates & _upstream_of(workflow, publisher), publisher


def test_the_release_pins_the_validator_because_its_tag_is_already_pushed() -> None:
    text = (_REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert re.search(r"twine==\d+\.\d+\.\d+", text), "release.yml runs an unpinned twine"


def test_ci_leaves_the_validator_floating_so_ecosystem_drift_fails_a_pull_request() -> None:
    text = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert _VALIDATE_COMMAND in text
    assert "twine==" not in text, "pinning here would hide the drift this gate exists to catch"
