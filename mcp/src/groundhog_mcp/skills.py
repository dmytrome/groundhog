import hashlib
import re
from collections.abc import Iterator, Sequence
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any, TypedDict

from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams

EXTENSION_ID = "io.modelcontextprotocol/skills"
_TTL_MS = 300_000
_SKILL_FILE = "SKILL.md"
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_KEY = re.compile(r"[a-z][a-z0-9-]*")
_PLAIN = re.compile(r"[^\s\"'\[\]{}>|&*!%@`#,?:-][^#]*")


class SkillResource(TypedDict):
    uri: str
    digest: str
    size: int


class Skill(TypedDict):
    uri: str
    frontmatter: dict[str, str]
    resources: list[SkillResource]


class GetSkillParams(RequestParams):
    uri: str


def frontmatter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("SKILL.md must open with frontmatter between --- lines")
    fields: dict[str, str] = {}
    for line in lines[1 : lines.index("---", 1)]:
        key, sep, value = line.partition(": ")
        if not sep or not _KEY.fullmatch(key) or not _PLAIN.fullmatch(value) or ": " in value:
            raise ValueError(f"frontmatter line is not a plain key: value pair: {line!r}")
        if key in fields:
            raise ValueError(f"frontmatter repeats {key!r}")
        fields[key] = value.rstrip()
    if not fields.get("name") or not fields.get("description"):
        raise ValueError("frontmatter needs name and description")
    return fields


def _files(root: Traversable, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            yield from _files(entry, f"{prefix}{entry.name}/")
        else:
            yield f"{prefix}{entry.name}", entry


class Skills(Extension):
    identifier = EXTENSION_ID

    def __init__(self, root: Traversable | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._resources: list[TextResource] = []
        base = root or resources.files(__package__) / "skills"
        for skill_dir in sorted(base.iterdir(), key=lambda e: e.name):
            if skill_dir.is_dir():
                self._load(skill_dir)

    def _load(self, skill_dir: Traversable) -> None:
        files = dict(_files(skill_dir))
        if _SKILL_FILE not in files:
            raise ValueError(f"{skill_dir.name} has no {_SKILL_FILE}")
        meta = frontmatter(files[_SKILL_FILE].read_bytes().decode())
        if meta["name"] != skill_dir.name or not _NAME.fullmatch(meta["name"]):
            raise ValueError(f"skill directory {skill_dir.name!r} must match its name")
        manifest: list[SkillResource] = []
        for path, file in files.items():
            raw = file.read_bytes()
            uri = f"skill://{skill_dir.name}/{path}"
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            manifest.append({"uri": uri, "digest": digest, "size": len(raw)})
            is_skill = path == _SKILL_FILE
            self._resources.append(
                TextResource(
                    uri=uri,
                    name=meta["name"] if is_skill else path,
                    description=meta["description"] if is_skill else None,
                    mime_type="text/markdown" if path.endswith(".md") else "text/plain",
                    text=raw.decode(),
                )
            )
        uri = f"skill://{skill_dir.name}/{_SKILL_FILE}"
        self._skills[uri] = {"uri": uri, "frontmatter": meta, "resources": manifest}

    def resources(self) -> Sequence[ResourceBinding]:
        return tuple(ResourceBinding(resource) for resource in self._resources)

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding("skills/list", PaginatedRequestParams, self._list),
            MethodBinding("skills/get", GetSkillParams, self._get),
        )

    async def _list(
        self, _ctx: ServerRequestContext[Any, Any], _params: PaginatedRequestParams
    ) -> dict[str, object]:
        return {
            "resultType": "complete",
            "skills": list(self._skills.values()),
            "ttlMs": _TTL_MS,
            "cacheScope": "public",
        }

    async def _get(
        self, _ctx: ServerRequestContext[Any, Any], params: GetSkillParams
    ) -> dict[str, object]:
        skill = self._skills.get(params.uri)
        if skill is None:
            raise MCPError(INVALID_PARAMS, f"unknown skill: {params.uri}")
        return {"resultType": "complete", "skill": skill, "ttlMs": _TTL_MS, "cacheScope": "public"}
