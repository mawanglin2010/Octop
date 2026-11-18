"""WorkBuddy expert market: catalog, preview and template conversion (fork).

``expert_center.json`` lists the experts; each one is a ``bundles/<plugin>.tar.gz``
plugin package::

    .codebuddy-plugin/plugin.json   names, descriptions, tags, quick prompts,
                                    optional mcpServers / dependencies
    agents/<agent>.md               the system prompt
    skills/<skill>/SKILL.md ...     bundled skills
    license/ avatars/ .mcp.json     licences, portraits, optional MCP servers

Only single-agent experts are offered (``expertType == "agent"``). Installing
converts the bundle into an Octop expert template (``manifest.json`` +
``SOUL.md`` + ``skills/``) under the expert market cache, where the expert
catalog picks it up; agents are then created through the normal template flow.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import tarfile
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from octop.infra.skills.skill_packages import SkillPackageError, SkillPackageTooLarge
from octop.infra.utils.frontmatter import parse_frontmatter
from octop.infra.workbuddy.archive import (
    EXPERT_MARKET_BASE,
    EXPERT_MARKET_BASE_ENV,
    fetch_cached,
    market_url,
)

TEMPLATE_PREFIX = "wb-"
_PLUGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_CENTER_BYTES = 32 * 1024 * 1024
_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_MAX_BUNDLE_FILES = 3_000
_MAX_UNPACKED_BYTES = 128 * 1024 * 1024
_PREVIEW_TEXT_BYTES = 256 * 1024
_BUNDLE_TTL_SECONDS = 24 * 3600

# WorkBuddy category id -> Octop expert scene (dashboard ``experts.scenes.*``).
CATEGORY_SCENES = {
    "01-ProductDesign": "design",
    "02-Engineering": "tech",
    "03-GameSpatial": "tech",
    "04-DataAI": "tech",
    "05-MarketingGrowth": "marketing",
    "06-ContentCreative": "content-creation",
    "07-SalesCommerce": "ecommerce",
    "08-FinanceInvestment": "finance",
    "09-OperationsHR": "hr",
    "10-ProjectQuality": "tech",
    "11-SecurityCompliance": "legal",
    "13-TencentZone": "tech",
    "15-BackToSchool": "education",
}


class WorkBuddyExpertNotFound(LookupError):
    pass


@dataclass(frozen=True)
class WorkBuddyExpert:
    plugin: str
    market_id: str
    nickname: dict[str, str]
    profession: dict[str, str]
    description: dict[str, str]
    category_id: str
    category: dict[str, str]
    tags: dict[str, list[str]]
    quick_prompts: dict[str, list[str]]
    avatar_url: str | None
    author: str
    updated_at: str

    @property
    def template_id(self) -> str:
        return f"{TEMPLATE_PREFIX}{self.plugin}"


@dataclass
class _Center:
    version: str
    experts: dict[str, WorkBuddyExpert] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    categories: list[dict[str, str]] = field(default_factory=list)


@dataclass
class McpEntry:
    name: str
    transport: str
    target: str  # URL or command
    status: str  # ok | internal | stdio | sse | needs_secret | invalid
    spec: dict[str, Any] | None = None  # custom-MCP spec when status == "ok"


@dataclass
class ExpertBundle:
    plugin_json: dict[str, Any]
    prompt: str
    skills: dict[str, list[tuple[str, bytes]]]
    licenses: list[tuple[str, bytes]]
    mcp: list[McpEntry]
    connectors: list[str]


_CENTER: _Center | None = None
_CENTER_LOCK = threading.Lock()


def _base_url() -> str:
    return market_url(EXPERT_MARKET_BASE_ENV, EXPERT_MARKET_BASE).rstrip("/")


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str | int | float) else ""


def _pair(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"zh": value.strip()} if value.strip() else {}
    if not isinstance(value, dict):
        return {}
    out = {k: _text(value.get(k)) for k in ("zh", "en")}
    return {k: v for k, v in out.items() if v}


def _pair_list(value: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"zh": [], "en": []}
    if not isinstance(value, list):
        return out
    for item in value:
        pair = _pair(item)
        for k in ("zh", "en"):
            if pair.get(k):
                out[k].append(pair[k])
    return out


def _load_center(force: bool = False) -> _Center:
    global _CENTER
    cached = fetch_cached(
        "expert_center.json",
        f"{_base_url()}/expert_center.json",
        force=force,
        max_bytes=_MAX_CENTER_BYTES,
    )
    with _CENTER_LOCK:
        if _CENTER is not None and _CENTER.version == cached.version and not force:
            return _CENTER
        _CENTER = _parse_center(cached.path.read_bytes(), cached.version)
        return _CENTER


def _parse_center(raw_bytes: bytes, version: str) -> _Center:
    raw = json.loads(raw_bytes)
    if not isinstance(raw, dict):
        raise SkillPackageError("WorkBuddy expert center is not an object")
    center = _Center(version=version)
    names: dict[str, dict[str, str]] = {}
    for cat in raw.get("categories") or []:
        if isinstance(cat, dict) and _text(cat.get("id")):
            names[_text(cat.get("id"))] = _pair(cat.get("name"))
            center.categories.append({"id": _text(cat.get("id")), **_pair(cat.get("name"))})
    base = _base_url()
    for item in raw.get("experts") or []:
        if not isinstance(item, dict) or item.get("expertType") != "agent":
            continue
        plugin = _text(item.get("plugin"))
        if not _PLUGIN_RE.match(plugin) or plugin in center.experts:
            continue
        avatar = _text(item.get("avatar"))
        author = item.get("author")
        center.experts[plugin] = WorkBuddyExpert(
            plugin=plugin,
            market_id=_text(item.get("id")),
            nickname=_pair(item.get("displayName")),
            profession=_pair(item.get("profession")),
            description=_pair(item.get("description")),
            category_id=_text(item.get("categoryId")),
            category=names.get(_text(item.get("categoryId")), {}),
            tags=_pair_list(item.get("tags")),
            quick_prompts=_pair_list(item.get("quickPrompts")),
            avatar_url=f"{base}/{avatar.lstrip('/')}" if avatar.startswith("/") else None,
            author=_text(author.get("name")) if isinstance(author, dict) else _text(author),
            updated_at=_text(item.get("updatedAt")),
        )
        center.order.append(plugin)
    return center


def list_experts(
    query: str = "", category: str = "", *, force: bool = False
) -> tuple[list[WorkBuddyExpert], list[dict[str, str]], str]:
    center = _load_center(force=force)
    q = query.strip().lower()
    out: list[WorkBuddyExpert] = []
    for plugin in center.order:
        expert = center.experts[plugin]
        if category and expert.category_id != category:
            continue
        if q:
            haystack = " ".join(
                [
                    plugin,
                    *expert.nickname.values(),
                    *expert.profession.values(),
                    *expert.description.values(),
                    *expert.tags["zh"],
                    *expert.tags["en"],
                ]
            ).lower()
            if q not in haystack:
                continue
        out.append(expert)
    return out, list(center.categories), center.version


def get_expert(plugin: str) -> WorkBuddyExpert:
    expert = _load_center().experts.get(plugin)
    if expert is None:
        raise WorkBuddyExpertNotFound(plugin)
    return expert


# --- bundle -----------------------------------------------------------------


def _bundle_path(plugin: str) -> Path:
    if not _PLUGIN_RE.match(plugin):
        raise WorkBuddyExpertNotFound(plugin)
    cached = fetch_cached(
        f"experts/{plugin}.tar.gz",
        f"{_base_url()}/bundles/{plugin}.tar.gz",
        ttl=_BUNDLE_TTL_SECONDS,
        max_bytes=_MAX_BUNDLE_BYTES,
    )
    return cached.path


def _member_path(name: str) -> str | None:
    """Normalized relative path, or ``None`` for unsafe names."""
    text = name.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/"):
        return None
    path = PurePosixPath(text)
    if ".." in path.parts or (path.parts and path.parts[0].endswith(":")):
        return None
    return path.as_posix()


def _read_tar(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(path, mode="r:gz") as tar:
        for member in tar:
            if not member.isreg():
                continue  # directories, symlinks, devices
            rel = _member_path(member.name)
            if rel is None or stat.S_ISLNK(member.mode):
                continue
            if len(files) >= _MAX_BUNDLE_FILES:
                raise SkillPackageTooLarge("expert bundle has too many files")
            total += member.size
            if total > _MAX_UNPACKED_BYTES:
                raise SkillPackageTooLarge("expert bundle is too large")
            fh = tar.extractfile(member)
            if fh is None:
                continue
            files[rel] = fh.read(member.size + 1)[: member.size]
    return files


def _resolve_rel(base: str, ref: str) -> str | None:
    joined = _member_path(f"{base}/{ref}" if base else ref)
    return joined


def _mcp_servers(plugin_json: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    deps = plugin_json.get("dependencies")
    deps = deps if isinstance(deps, dict) else {}
    declared = deps.get("mcpServers", plugin_json.get("mcpServers"))
    data: Any = None
    if isinstance(declared, dict):
        data = declared
    elif isinstance(declared, str):
        rel = _resolve_rel("", declared)
        if rel and rel in files:
            data = json.loads(files[rel])
    elif ".mcp.json" in files:
        data = json.loads(files[".mcp.json"])
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]
    return data if isinstance(data, dict) else {}


_INTERNAL_HOST = re.compile(r"(\.woa\.com|\.local|\.internal|\.oa\.com)$", re.IGNORECASE)
_NAME_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def custom_mcp_name(name: str) -> str:
    safe = _NAME_SAFE.sub("-", name).strip("-_").lower() or "mcp"
    return f"{TEMPLATE_PREFIX}{safe}"[:64]


def classify_mcp(name: str, cfg: Any) -> McpEntry:
    if not isinstance(cfg, dict):
        return McpEntry(name, "?", "", "invalid")
    raw_type = str(cfg.get("type") or cfg.get("transport") or "").strip()
    if cfg.get("command") or raw_type == "stdio":
        raw_args = cfg.get("args")
        args: list[Any] = raw_args if isinstance(raw_args, list) else []
        target = " ".join([str(cfg.get("command") or ""), *map(str, args)]).strip()
        return McpEntry(name, "stdio", target, "stdio")
    url = _text(cfg.get("url"))
    transport = "sse" if raw_type.lower() == "sse" else "streamable_http"
    if not url:
        return McpEntry(name, transport, "", "invalid")
    if transport == "sse":
        return McpEntry(name, transport, url, "sse")
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not host or _INTERNAL_HOST.search(host):
        return McpEntry(name, transport, url, "internal")
    raw_headers = cfg.get("headers")
    headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
    if any("${" in str(v) for v in headers.values()):
        return McpEntry(name, transport, url, "needs_secret")
    spec: dict[str, Any] = {"transport": "streamable_http", "url": url, "enabled": True}
    if headers:
        spec["headers"] = {str(k): str(v) for k, v in headers.items()}
    return McpEntry(name, transport, url, "ok", spec)


def read_bundle(plugin: str) -> ExpertBundle:
    files = _read_tar(_bundle_path(plugin))
    raw_manifest = files.get(".codebuddy-plugin/plugin.json")
    if raw_manifest is None:
        raise SkillPackageError("expert bundle has no plugin.json")
    plugin_json = json.loads(raw_manifest)
    if not isinstance(plugin_json, dict) or plugin_json.get("expertType", "agent") != "agent":
        raise SkillPackageError("only single-agent experts are supported")

    agent_rel = None
    agents = plugin_json.get("agents")
    if isinstance(agents, list) and agents:
        agent_rel = _resolve_rel("", str(agents[0]))
    if not agent_rel or agent_rel not in files:
        agent_name = _text(plugin_json.get("agentName"))
        agent_rel = f"agents/{agent_name}.md" if agent_name else None
    if not agent_rel or agent_rel not in files:
        raise SkillPackageError("expert bundle has no agent prompt")
    _meta, body = parse_frontmatter(files[agent_rel].decode("utf-8", errors="replace"))

    skills: dict[str, list[tuple[str, bytes]]] = {}
    declared = plugin_json.get("skills")
    skill_dirs = (
        [
            d
            for d in (_resolve_rel("", str(s)) for s in declared or [])
            if d and d.startswith("skills/")
        ]
        if isinstance(declared, list)
        else []
    )
    if not skill_dirs:
        skill_dirs = sorted(
            {
                "/".join(p.split("/")[:2])
                for p in files
                if p.startswith("skills/") and p.count("/") >= 2
            }
        )
    for skill_dir in skill_dirs:
        name = skill_dir.split("/", 1)[1]
        if "/" in name or f"{skill_dir}/SKILL.md" not in files:
            continue
        prefix = f"{skill_dir}/"
        skills[name] = [
            (p[len(prefix) :], data) for p, data in files.items() if p.startswith(prefix)
        ]

    licenses = [(p.split("/", 1)[1], d) for p, d in files.items() if p.startswith("license/")]
    mcp = [classify_mcp(n, c) for n, c in _mcp_servers(plugin_json, files).items()]
    deps = plugin_json.get("dependencies")
    connectors = [
        c.strip()
        for c in ((deps or {}).get("connectors") or [] if isinstance(deps, dict) else [])
        if isinstance(c, str) and c.strip()
    ]
    return ExpertBundle(plugin_json, body.strip() + "\n", skills, licenses, mcp, connectors)


def preview(plugin: str) -> dict[str, Any]:
    bundle = read_bundle(plugin)
    skills = []
    for name, files in sorted(bundle.skills.items()):
        skill_md = dict(files).get("SKILL.md", b"").decode("utf-8", errors="replace")
        meta, _ = parse_frontmatter(skill_md)
        skills.append(
            {
                "name": name,
                "description": _text(meta.get("description"))[:400],
                "file_count": len(files),
            }
        )
    return {
        "prompt": bundle.prompt[:_PREVIEW_TEXT_BYTES],
        "skills": skills,
        "mcp": [
            {"name": m.name, "transport": m.transport, "target": m.target, "status": m.status}
            for m in bundle.mcp
        ],
        "connectors": bundle.connectors,
    }


# --- template ----------------------------------------------------------------


def _manifest(expert: WorkBuddyExpert, bundle: ExpertBundle) -> dict[str, Any]:
    pj = bundle.plugin_json
    profession = _pair(pj.get("profession")) or expert.profession
    nickname = _pair(pj.get("displayName")) or expert.nickname
    description = _pair(pj.get("displayDescription")) or expert.description
    prompts = _pair_list(pj.get("quickPrompts"))
    if not prompts["zh"] and not prompts["en"]:
        prompts = expert.quick_prompts
    label = {
        k: (
            f"{profession[k]}·{nickname[k]}"
            if nickname.get(k) and nickname[k] not in profession[k]
            else profession[k]
        )
        for k in profession
    }
    welcome = {
        "zh": f"你好，我是{nickname.get('zh') or profession.get('zh', '')}。"
        + description.get("zh", ""),
        "en": f"Hi, I'm {nickname.get('en') or profession.get('en', '')}. "
        + description.get("en", ""),
    }
    return {
        "id": expert.template_id,
        "scene": CATEGORY_SCENES.get(expert.category_id, ""),
        "label": label or {"zh": expert.plugin, "en": expert.plugin},
        "description": description,
        "welcome_message": welcome,
        "icon_name": "sparkles",
        "icon_url": expert.avatar_url,
        "prompt_files": ["SOUL.md"],
        "task_examples": prompts,
        "source": {
            "type": "workbuddy",
            "plugin": expert.plugin,
            "market_id": expert.market_id,
            "version": _text(pj.get("version")),
        },
    }


def install_template(plugin: str, cache_root: Path) -> tuple[str, ExpertBundle]:
    """Write the Octop template for ``plugin`` under ``cache_root``; return its id."""
    expert = get_expert(plugin)
    bundle = read_bundle(plugin)
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / expert.template_id
    with tempfile.TemporaryDirectory(dir=cache_root) as tmp:
        stage = Path(tmp) / expert.template_id
        stage.mkdir()
        (stage / "manifest.json").write_text(
            json.dumps(_manifest(expert, bundle), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (stage / "SOUL.md").write_text(bundle.prompt, encoding="utf-8")
        for name, files in bundle.skills.items():
            for rel, data in files:
                dest = stage / "skills" / name / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        for rel, data in bundle.licenses:
            dest = stage / "licenses" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if target.exists():
            shutil.rmtree(target)
        stage.rename(target)
    return expert.template_id, bundle


def reset_cache_for_tests() -> None:
    global _CENTER
    with _CENTER_LOCK:
        _CENTER = None


def ensure_custom_mcp(
    svc: Any, user_id: int, entries: list[McpEntry]
) -> tuple[list[str], list[dict[str, str]], bool]:
    """Add the expert's public MCP servers to the user's custom MCP document.

    A server whose URL is already configured is reused. Returns the server
    names to mount on the new agent, the skipped entries with a reason, and
    whether the document changed.
    """
    from octop.infra.connectors.service import ConnectorNameTakenError  # noqa: PLC0415

    existing: dict[str, Any] = svc.get_custom_servers(user_id)
    merged = dict(existing)
    names: list[str] = []
    skipped: list[dict[str, str]] = []
    added: list[str] = []
    for entry in entries:
        if entry.status != "ok" or entry.spec is None:
            skipped.append({"name": entry.name, "reason": entry.status})
            continue
        reuse = next(
            (
                n
                for n, spec in merged.items()
                if isinstance(spec, dict) and spec.get("url") == entry.spec["url"]
            ),
            None,
        )
        if reuse is not None:
            if reuse not in names:
                names.append(reuse)
            continue
        base = custom_mcp_name(entry.name)
        name, i = base, 2
        while name in merged:
            name, i = f"{base}-{i}", i + 1
        merged[name] = {**entry.spec, "display_name": f"{entry.name} (WorkBuddy)"[:64]}
        names.append(name)
        added.append(name)
    if added:
        try:
            svc.put_custom_servers(user_id, merged)
        except ConnectorNameTakenError:
            for name in added:
                merged[name].pop("display_name", None)
            svc.put_custom_servers(user_id, merged)
    active = set(svc.list_active_mcp_server_names(user_id))
    for name in [n for n in names if n not in active]:
        names.remove(name)
        skipped.append({"name": name, "reason": "connectors_disabled"})
    return names, skipped, bool(added)
