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


_BUILD_REQUIREMENTS = "build-requirements.txt"
_BUILD_COMMAND = f"uv build --build-constraint {_BUILD_REQUIREMENTS}"
_VALIDATE_COMMAND = "twine check --strict dist/*"
_PUBLISHING_MARKERS = (
    "pypa/gh-action-pypi-publish",
    "softprops/action-gh-release",
    "mcp-publisher publish",
)


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
            assert dep in workflow["jobs"], f"{job} needs a job that does not exist: {dep}"
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return seen


def _gate_commands(workflow: dict) -> list[str]:
    return [
        cmd for job in workflow["jobs"].values() for cmd in _runs(job) if _VALIDATE_COMMAND in cmd
    ]


def _publishing_jobs(workflow: dict) -> set[str]:
    return {
        name
        for name, job in workflow["jobs"].items()
        if any(marker in yaml.safe_dump(job) for marker in _PUBLISHING_MARKERS)
    }


def test_the_build_backend_is_pinned_to_an_exact_version() -> None:
    constraints = (_REPO_ROOT / "mcp" / _BUILD_REQUIREMENTS).read_text()
    assert re.search(r"^hatchling==\d+\.\d+\.\d+$", constraints, re.MULTILINE), constraints


@pytest.mark.parametrize("workflow", ("ci.yml", "release.yml"))
def test_every_workflow_builds_against_the_pinned_backend(workflow: str) -> None:
    assert any(
        _BUILD_COMMAND in cmd for job in _workflow(workflow)["jobs"].values() for cmd in _runs(job)
    ), workflow


@pytest.mark.parametrize("workflow", ("ci.yml", "release.yml"))
def test_every_workflow_validates_the_built_distribution(workflow: str) -> None:
    assert _gate_commands(_workflow(workflow)), workflow


def test_nothing_that_publishes_can_run_before_the_gate() -> None:
    workflow = _workflow("release.yml")
    gates = {
        name
        for name, job in workflow["jobs"].items()
        if any(_VALIDATE_COMMAND in cmd for cmd in _runs(job))
    }
    assert gates, "no job runs the distribution gate"
    publishers = _publishing_jobs(workflow)
    assert publishers, "no job was recognised as publishing"
    for publisher in publishers:
        assert gates & _upstream_of(workflow, publisher), publisher


def test_the_release_pins_the_validator_because_its_tag_is_already_pushed() -> None:
    for command in _gate_commands(_workflow("release.yml")):
        assert re.search(r"twine==\d+\.\d+\.\d+", command), command


def test_ci_leaves_the_validator_floating_so_ecosystem_drift_fails_a_pull_request() -> None:
    for command in _gate_commands(_workflow("ci.yml")):
        assert "twine==" not in command, command


@pytest.mark.parametrize("workflow", ("ci.yml", "release.yml"))
def test_every_workflow_fails_on_unformatted_code(workflow: str) -> None:
    assert any(
        "ruff format --check" in cmd
        for job in _workflow(workflow)["jobs"].values()
        for cmd in _runs(job)
    ), workflow


def _dependabot() -> dict:
    return yaml.safe_load((_REPO_ROOT / ".github" / "dependabot.yml").read_text())


def _channel(ecosystem: str) -> list[dict]:
    return [u for u in _dependabot()["updates"] if u["package-ecosystem"] == ecosystem]


def test_the_sha_pinned_actions_have_an_update_channel() -> None:
    assert _channel("github-actions"), "every workflow pins actions by SHA and nothing bumps them"


def test_the_locked_python_dependencies_have_an_update_channel() -> None:
    assert any(u["directory"].rstrip("/") == "/mcp" for u in _channel("uv"))


def test_the_build_backend_pin_has_an_update_channel() -> None:
    assert any(u["directory"].rstrip("/") == "/mcp" for u in _channel("pip"))


def test_the_build_requirements_filename_is_one_dependabot_discovers() -> None:
    assert "requirements" in _BUILD_REQUIREMENTS, _BUILD_REQUIREMENTS
    assert _BUILD_REQUIREMENTS.endswith(".txt"), _BUILD_REQUIREMENTS


def test_the_declared_sdk_floor_excludes_versions_the_code_cannot_import() -> None:
    with (_REPO_ROOT / "mcp" / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    specs = [d for d in deps if re.split(r"[<>=!~ ]", d, maxsplit=1)[0].strip() == "mcp"]
    assert len(specs) == 1, specs
    floor = re.search(r">=\s*(\d+)", specs[0])
    assert floor is not None, specs[0]
    assert int(floor.group(1)) >= 2, (
        f"{specs[0]} admits an SDK predating mcp.server.mcpserver, which server.py imports"
    )


def _workflow_jobs() -> list[tuple[str, str, dict]]:
    return [
        (path.name, name, job)
        for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        for name, job in _workflow(path.name)["jobs"].items()
    ]


def _steps_using(job: dict, action: str) -> list[dict]:
    return [
        step for step in job.get("steps", []) if str(step.get("uses", "")).startswith(f"{action}@")
    ]


def test_only_a_job_that_pushes_keeps_the_checkout_credential() -> None:
    for workflow, name, job in _workflow_jobs():
        pushes = any("git push" in cmd for cmd in _runs(job))
        for step in _steps_using(job, "actions/checkout"):
            kept = step.get("with", {}).get("persist-credentials")
            assert kept is pushes, f"{workflow}:{name} persist-credentials={kept!r}"


def test_the_release_builds_without_a_restored_cache() -> None:
    steps = [
        step
        for job in _workflow("release.yml")["jobs"].values()
        for step in _steps_using(job, "astral-sh/setup-uv")
    ]
    assert steps
    assert all(step.get("with", {}).get("enable-cache") is False for step in steps), steps


def test_every_dockerfile_has_an_update_channel() -> None:
    watched = {
        d.rstrip("/")
        for u in _channel("docker")
        for d in u.get("directories", [u.get("directory", "")])
    }
    dockerfiles = {
        p.parent.relative_to(_REPO_ROOT).as_posix()
        for p in _REPO_ROOT.rglob("Dockerfile")
        if not {".venv", "node_modules", ".git"} & set(p.parts)
    }
    found = {"" if d == "." else f"/{d}" for d in dockerfiles}
    assert found and found <= watched, found - watched


def test_every_pull_request_is_scanned_for_secrets_workflow_risks_and_new_advisories() -> None:
    workflow = _workflow("security.yml")

    commands = " ".join(cmd for job in workflow["jobs"].values() for cmd in _runs(job))

    assert "pull_request" in workflow[True]
    for scanner in ("zizmor", "gitleaks git", "pip-audit"):
        assert scanner in commands, scanner


def test_every_binary_a_workflow_downloads_is_checked_against_a_pinned_digest() -> None:
    for workflow, name, job in _workflow_jobs():
        for cmd in _runs(job):
            if re.search(r"curl [^\n]*-o ", cmd):
                assert re.search(r"[0-9a-f]{64} [^\n]*\| sha256sum -c -", cmd), f"{workflow}:{name}"
