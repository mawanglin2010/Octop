"""WorkBuddy skill market: catalog, preview and install files (fork).

The market is one zip (``skill-marketplace.zip``)::

    .codebuddy-skill/marketplace.json   catalog (names, zh/en text, tags, examples)
    skills/<source>/SKILL.md ...        one directory per skill
    icons/<source>.(svg|png|jpg)        optional icon

Skill files are read straight from the cached zip; nothing is extracted to
disk. Installing goes through Octop's normal skill pipeline
(:func:`octop.infra.skills.skill_packages.resolve_skill_package`), which
enforces the file count and size limits.
"""

from __future__ import annotations

import json
import re
import stat
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from octop.infra.skills.skill_packages import (
    MAX_SKILL_BYTES,
    MAX_SKILL_FILES,
    ResolvedSkillPackage,
    SkillPackageError,
    SkillPackageTooLarge,
    resolve_skill_package,
    validate_skill_slug,
)
from octop.infra.utils.frontmatter import parse_frontmatter
from octop.infra.workbuddy.archive import (
    SKILL_MARKET_URL,
    SKILL_MARKET_URL_ENV,
    fetch_cached,
    market_url,
)

CATALOG_PATH = ".codebuddy-skill/marketplace.json"
SOURCE = "workbuddy"
_MAX_CATALOG_BYTES = 8 * 1024 * 1024
_MAX_ICON_BYTES = 8 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200.0
_PREVIEW_TEXT_BYTES = 256 * 1024
_ICON_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_WORKBUDDY_HINT = re.compile(r"workbuddy|codebuddy", re.IGNORECASE)


class WorkBuddySkillNotFound(LookupError):
    pass


@dataclass(frozen=True)
class WorkBuddySkill:
    slug: str
    name: dict[str, str]
    summary: dict[str, str]
    description: str
    category: dict[str, str]
    tags: dict[str, list[str]]
    examples: dict[str, list[str]]
    featured: bool
    version: str
    has_icon: bool
    mentions_workbuddy: bool
    file_count: int
    total_bytes: int


@dataclass
class _Catalog:
    version: str
    zip_path: str
    base: str  # "" or "<top-dir>/"
    skills: dict[str, WorkBuddySkill] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    categories: list[dict[str, str]] = field(default_factory=list)
    icons: dict[str, str] = field(default_factory=dict)  # slug -> zip member
    icon_cache: dict[str, bytes] = field(default_factory=dict)


_CACHE: _Catalog | None = None
_CACHE_LOCK = threading.Lock()
_ICON_LOCK = threading.Lock()


def _safe_member(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    return ".." not in PurePosixPath(name).parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _decoded_name(info: zipfile.ZipInfo) -> str:
    """Entry name repaired from WorkBuddy's zip encoding.

    Their zips store UTF-8 names without the UTF-8 flag, so Python reads them
    as CP437. Some names were mangled that way twice before being stored. Undo
    CP437->UTF-8 rounds until the name no longer round-trips; a real CJK name
    stops the loop because it cannot be encoded as CP437.
    """
    name = info.filename
    first = not (info.flag_bits & 0x800)
    for _ in range(3):
        try:
            raw = name.encode("cp437")
        except UnicodeEncodeError:
            break
        try:
            fixed = raw.decode("utf-8")
        except UnicodeDecodeError:
            if not first:
                break
            try:
                fixed = raw.decode("gbk")
            except UnicodeDecodeError:
                break
        if fixed == name:
            break
        name, first = fixed, False
    return name


def _entries(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Safe regular-file entries with decoded names (``orig_filename`` untouched)."""
    out: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        info.filename = _decoded_name(info)
        if info.is_dir() or _is_symlink(info) or not _safe_member(info.filename):
            continue
        out.append(info)
    return out


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise SkillPackageTooLarge(f"{info.filename} is too large")
    if info.compress_size and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
        raise SkillPackageError(f"{info.filename} has a suspicious compression ratio")
    with zf.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise SkillPackageTooLarge(f"{info.filename} is too large")
    return data


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str | int | float) else ""


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, str) and v.strip()]


def _pair(zh: Any, en: Any) -> dict[str, str]:
    out = {"zh": _text(zh), "en": _text(en)}
    return {k: v for k, v in out.items() if v}


def _load_catalog(force: bool = False) -> _Catalog:
    global _CACHE
    cached = fetch_cached(
        "skill-marketplace.zip",
        market_url(SKILL_MARKET_URL_ENV, SKILL_MARKET_URL),
        force=force,
    )
    with _CACHE_LOCK:
        if _CACHE is not None and _CACHE.version == cached.version and not force:
            return _CACHE
        _CACHE = _parse_catalog(str(cached.path), cached.version)
        return _CACHE


def _parse_catalog(zip_path: str, version: str) -> _Catalog:
    with zipfile.ZipFile(zip_path) as zf:
        infos = _entries(zf)
        catalog_info = next((i for i in infos if i.filename.endswith(CATALOG_PATH)), None)
        if catalog_info is None:
            raise SkillPackageError("WorkBuddy skill market has no catalog")
        base = catalog_info.filename[: -len(CATALOG_PATH)]
        raw = json.loads(_read_member(zf, catalog_info, _MAX_CATALOG_BYTES))
        if not isinstance(raw, dict):
            raise SkillPackageError("WorkBuddy skill catalog is not an object")

        per_skill: dict[str, list[zipfile.ZipInfo]] = {}
        icons: dict[str, str] = {}
        skills_prefix = f"{base}skills/"
        icons_prefix = f"{base}icons/"
        for info in infos:
            name = info.filename
            if name.startswith(skills_prefix):
                slug = name[len(skills_prefix) :].split("/", 1)[0]
                per_skill.setdefault(slug, []).append(info)
            elif name.startswith(icons_prefix) and "/" not in name[len(icons_prefix) :]:
                stem = PurePosixPath(name).stem
                if PurePosixPath(name).suffix.lower() in _ICON_TYPES:
                    icons.setdefault(stem, name)

        catalog = _Catalog(version=version, zip_path=zip_path, base=base, icons=icons)
        zh_tags = _str_list(raw.get("tags_zh"))
        en_tags = _str_list(raw.get("tags_en"))
        for i, zh in enumerate(zh_tags):
            catalog.categories.append({"zh": zh, "en": en_tags[i] if i < len(en_tags) else zh})
        for entry in raw.get("skills") or []:
            if not isinstance(entry, dict):
                continue
            slug = _text(entry.get("source"))
            try:
                slug = validate_skill_slug(slug)
            except SkillPackageError:
                continue
            members = per_skill.get(slug) or []
            skill_md = next(
                (m for m in members if m.filename == f"{skills_prefix}{slug}/SKILL.md"), None
            )
            if skill_md is None or slug in catalog.skills:
                continue
            try:
                head = _read_member(zf, skill_md, _PREVIEW_TEXT_BYTES)
            except SkillPackageError:
                continue
            tags_zh = _str_list(entry.get("tags_zh"))
            tags_en = _str_list(entry.get("tags_en"))
            catalog.skills[slug] = WorkBuddySkill(
                slug=slug,
                name=_pair(entry.get("name_zh") or entry.get("name"), entry.get("name_en")),
                summary=_pair(entry.get("description_zh"), entry.get("description_en")),
                description=_text(entry.get("description")),
                category=_pair(tags_zh[0] if tags_zh else "", tags_en[0] if tags_en else ""),
                tags={"zh": tags_zh, "en": tags_en},
                examples={
                    "zh": _str_list(entry.get("examples_zh")),
                    "en": _str_list(entry.get("examples_en")),
                },
                featured=bool(entry.get("featured")),
                version=_text(entry.get("version")),
                has_icon=slug in icons,
                mentions_workbuddy=bool(
                    _WORKBUDDY_HINT.search(head.decode("utf-8", errors="replace"))
                ),
                file_count=len(members),
                total_bytes=sum(m.file_size for m in members),
            )
            catalog.order.append(slug)
    return catalog


def list_skills(
    query: str = "", category: str = "", *, force: bool = False
) -> tuple[list[WorkBuddySkill], list[dict[str, str]], str]:
    """Catalog entries matching ``query`` / ``category`` plus the category list."""
    catalog = _load_catalog(force=force)
    q = query.strip().lower()
    cat = category.strip()
    out: list[WorkBuddySkill] = []
    for slug in catalog.order:
        skill = catalog.skills[slug]
        if cat and cat not in skill.tags["zh"] and cat not in skill.tags["en"]:
            continue
        if q:
            haystack = " ".join(
                [
                    slug,
                    skill.description,
                    *skill.name.values(),
                    *skill.summary.values(),
                    *skill.tags["zh"],
                    *skill.tags["en"],
                ]
            ).lower()
            if q not in haystack:
                continue
        out.append(skill)
    out.sort(key=lambda s: (not s.featured,))
    return out, list(catalog.categories), catalog.version


def get_skill(slug: str) -> WorkBuddySkill:
    catalog = _load_catalog()
    skill = catalog.skills.get(slug)
    if skill is None:
        raise WorkBuddySkillNotFound(slug)
    return skill


def _members(catalog: _Catalog, zf: zipfile.ZipFile, slug: str) -> list[zipfile.ZipInfo]:
    prefix = f"{catalog.base}skills/{slug}/"
    return [i for i in _entries(zf) if i.filename.startswith(prefix)]


def skill_preview(slug: str) -> dict[str, Any]:
    """``SKILL.md`` text and the file list for the detail drawer."""
    catalog = _load_catalog()
    if slug not in catalog.skills:
        raise WorkBuddySkillNotFound(slug)
    prefix = f"{catalog.base}skills/{slug}/"
    with zipfile.ZipFile(catalog.zip_path) as zf:
        members = _members(catalog, zf, slug)
        skill_md = next((m for m in members if m.filename == f"{prefix}SKILL.md"), None)
        text = ""
        if skill_md is not None:
            text = _read_member(zf, skill_md, _PREVIEW_TEXT_BYTES).decode("utf-8", errors="replace")
    files = sorted(
        ({"path": m.filename[len(prefix) :], "size": m.file_size} for m in members),
        key=lambda f: str(f["path"]),
    )
    return {"skill_md": text, "files": files}


def skill_icon(slug: str) -> tuple[bytes, str]:
    catalog = _load_catalog()
    member = catalog.icons.get(slug)
    if member is None:
        raise WorkBuddySkillNotFound(slug)
    media_type = _ICON_TYPES[PurePosixPath(member).suffix.lower()]
    cached = catalog.icon_cache.get(slug)
    if cached is not None:
        return cached, media_type
    with _ICON_LOCK:
        if catalog.icon_cache:
            data = catalog.icon_cache.get(slug)
            if data is None:
                raise WorkBuddySkillNotFound(slug)
            return data, media_type
        # First icon: load them all in one pass instead of reopening the zip
        # for every card on the page.
        with zipfile.ZipFile(catalog.zip_path) as zf:
            by_name = {i.filename: i for i in _entries(zf)}
            for icon_slug, icon_member in catalog.icons.items():
                info = by_name.get(icon_member)
                if info is None:
                    continue
                try:
                    catalog.icon_cache[icon_slug] = _read_member(zf, info, _MAX_ICON_BYTES)
                except SkillPackageError:
                    continue
    data = catalog.icon_cache.get(slug)
    if data is None:
        raise WorkBuddySkillNotFound(slug)
    return data, media_type


def skill_files(slug: str) -> list[tuple[str, bytes]]:
    """All files of one skill as ``(relative path, bytes)``."""
    catalog = _load_catalog()
    if slug not in catalog.skills:
        raise WorkBuddySkillNotFound(slug)
    prefix = f"{catalog.base}skills/{slug}/"
    out: list[tuple[str, bytes]] = []
    total = 0
    with zipfile.ZipFile(catalog.zip_path) as zf:
        members = _members(catalog, zf, slug)
        if len(members) > MAX_SKILL_FILES:
            raise SkillPackageTooLarge("skill package has too many files")
        for info in members:
            data = _read_member(zf, info, MAX_SKILL_BYTES)
            total += len(data)
            if total > MAX_SKILL_BYTES:
                raise SkillPackageTooLarge("skill package size exceeds 64 MB")
            out.append((info.filename[len(prefix) :], data))
    return out


def with_workbuddy_metadata(
    content: str,
    *,
    label: dict[str, str],
    summary: dict[str, str],
    icon_url: str = "",
) -> str:
    """Record the WorkBuddy origin and zh/en presentation in ``metadata.octop``."""
    meta, body = parse_frontmatter(content)
    metadata = meta.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    octop_meta = metadata.get("octop")
    octop_meta = dict(octop_meta) if isinstance(octop_meta, dict) else {}
    octop_meta["source"] = SOURCE
    if label:
        octop_meta["label"] = label
    if summary:
        octop_meta["summary"] = summary
    if icon_url:
        octop_meta["icon_url"] = icon_url
    metadata["octop"] = octop_meta
    meta["metadata"] = metadata
    dumped = yaml.safe_dump(
        meta, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).rstrip()
    rendered = f"---\n{dumped}\n---\n"
    if body:
        rendered += f"\n{body}"
    if content.endswith("\n") and not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def icon_api_url(slug: str) -> str:
    return f"/api/workbuddy/skills/{slug}/icon"


def resolve_install_package(slug: str) -> ResolvedSkillPackage:
    """Files of ``slug`` normalized into Octop's canonical skill package."""
    skill = get_skill(slug)
    files: list[tuple[str, bytes]] = []
    for rel, data in skill_files(slug):
        if rel == "SKILL.md":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                # No icon_url: installed-skill views only accept absolute
                # http(s) icons, and the market icon lives behind our API.
                data = with_workbuddy_metadata(
                    text, label=skill.name, summary=skill.summary
                ).encode("utf-8")
        files.append((rel, data))
    return resolve_skill_package(slug=slug, files=files, source=SOURCE)


def reset_cache_for_tests() -> None:
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None
