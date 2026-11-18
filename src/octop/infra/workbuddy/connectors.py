"""WorkBuddy connector market: catalog, preview and custom-MCP install (fork).

``connector-plugins.zip`` holds one plugin per connector::

    connectors/<id>/.codebuddy-plugin/plugin.json   display text, auth setup
    connectors/<id>/mcp.json                         MCP servers
    connectors/<id>/ai.workbuddy/token-schema.json   credential form (optional)
    connectors/<id>/ai.workbuddy/assets/icon.*       icon (optional)
    connectors/<id>/ai.workbuddy/cli.json            CLI-only connectors
    connectors/<id>/skills/...                       usage skills (not installed)

Only remote streamable-HTTP servers are installed; they become entries of the
installing user's custom MCP document. Credentials from the token form are
placed where WorkBuddy's ``setup`` says (headers with a prefix, or ``${FIELD}``
in a URL template). OAuth connectors are stored with Octop's "authorize first"
marker so the custom-MCP page runs the OAuth flow.
"""

from __future__ import annotations

import json
import re
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from octop.infra.skills.skill_packages import SkillPackageError
from octop.infra.workbuddy.archive import (
    CONNECTOR_MARKET_URL,
    CONNECTOR_MARKET_URL_ENV,
    fetch_cached,
    market_url,
)
from octop.infra.workbuddy.experts import custom_mcp_name
from octop.infra.workbuddy.skills import _entries, _read_member

CATALOG_PATH = ".codebuddy-plugin/marketplace.json"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")
_SMALL = 1024 * 1024
_ICON_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_INTERNAL_HOST = re.compile(r"(\.woa\.com|\.local|\.internal|\.oa\.com)$", re.IGNORECASE)

# Why a connector cannot be installed here (``supported`` = installable).
REASONS = (
    "supported",
    "cli",
    "stdio",
    "sse",
    "server_side",
    "internal",
    "no_mcp",
    "unsupported_auth",
)


class WorkBuddyConnectorNotFound(LookupError):
    pass


class WorkBuddyConnectorInputError(ValueError):
    pass


@dataclass(frozen=True)
class TokenField:
    key: str
    label: dict[str, str]
    placeholder: dict[str, str]
    description: dict[str, str]
    secret: bool
    required: bool


@dataclass(frozen=True)
class ServerPlan:
    name: str
    url_template: str
    headers: dict[str, str]  # static headers (may hold ${FIELD})
    header_fields: dict[str, tuple[str, str]]  # header -> (field, prefix)


@dataclass(frozen=True)
class WorkBuddyConnector:
    id: str
    name: dict[str, str]
    description: dict[str, str]
    examples: dict[str, list[str]]
    status: str
    auth: str  # none | token | oauth
    servers: tuple[ServerPlan, ...]
    fields: tuple[TokenField, ...]
    form: dict[str, Any]  # title / description / doc link of the token form
    skills: tuple[str, ...]
    icon_member: str | None


@dataclass
class _Catalog:
    version: str
    zip_path: str
    connectors: dict[str, WorkBuddyConnector] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    icon_cache: dict[str, bytes] = field(default_factory=dict)


_CACHE: _Catalog | None = None
_LOCK = threading.Lock()


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str | int | float) else ""


def _pair(zh: Any, en: Any) -> dict[str, str]:
    out = {"zh": _text(zh), "en": _text(en)}
    return {k: v for k, v in out.items() if v}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    return (
        [v.strip() for v in value if isinstance(v, str) and v.strip()]
        if isinstance(value, list)
        else []
    )


def _json(files: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile, rel: str) -> Any:
    info = files.get(rel)
    if info is None:
        return None
    try:
        return json.loads(_read_member(zf, info, _SMALL))
    except (ValueError, SkillPackageError):
        return None


def _fields(schema: Any) -> tuple[tuple[TokenField, ...], dict[str, Any]]:
    if not isinstance(schema, dict):
        return (), {}
    out: list[TokenField] = []
    for raw in schema.get("fields") or []:
        if not isinstance(raw, dict) or not _text(raw.get("key")):
            continue
        out.append(
            TokenField(
                key=_text(raw.get("key")),
                label=_pair(raw.get("label"), raw.get("label_en")),
                placeholder=_pair(raw.get("placeholder"), raw.get("placeholder_en")),
                description=_pair(raw.get("description"), raw.get("description_en")),
                secret=_text(raw.get("type")) == "password",
                required=raw.get("required") is not False,
            )
        )
    form = {
        "title": _pair(schema.get("title"), schema.get("title_en")),
        "description": _pair(schema.get("description"), schema.get("description_en")),
        "doc_url": _text(schema.get("docUrl")),
        "doc_label": _pair(schema.get("docLabel"), schema.get("docLabel_en")),
    }
    return tuple(out), form


def _resolve(base: str, ref: str) -> str:
    rel = ref[2:] if ref.startswith("./") else ref
    return f"{base}{rel}"


def _connector(
    cid: str, base: str, files: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile
) -> WorkBuddyConnector | None:
    plugin = _json(files, zf, f"{base}.codebuddy-plugin/plugin.json")
    if not isinstance(plugin, dict):
        return None
    ext = (plugin.get("extensions") or {}).get("ai.workbuddy") or {}
    ext = ext if isinstance(ext, dict) else {}
    display = _dict(ext.get("display"))
    legacy = _dict(ext.get("connector"))
    name = _pair(display.get("name_zh") or display.get("name"), display.get("name_en")) or _pair(
        legacy.get("name"), legacy.get("name_en")
    )
    description = _pair(display.get("description_zh"), display.get("description_en")) or _pair(
        plugin.get("description"), plugin.get("description")
    )
    examples = {"zh": _list(display.get("examples_zh")), "en": _list(display.get("examples_en"))}

    mode = _text((ext.get("auth") or {}).get("mode") if isinstance(ext.get("auth"), dict) else "")
    mode = mode or _text(legacy.get("auth_mode"))
    setup = (
        ((ext.get("setup") or {}).get("mcpServers") or {})
        if isinstance(ext.get("setup"), dict)
        else {}
    )

    raw_servers: dict[str, Any] = {}
    for rel in ("mcp.json", ".mcp.json"):
        data = _json(files, zf, f"{base}{rel}")
        if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
            raw_servers.update(data["mcpServers"])
            break
    templated = ext.get("urlTemplatedMcpServers")
    templated = templated if isinstance(templated, dict) else {}

    schema_refs: list[str] = []
    plans: list[ServerPlan] = []
    transports: set[str] = set()
    for sname, cfg in {**raw_servers, **templated}.items():
        if not isinstance(cfg, dict):
            continue
        typ = _text(cfg.get("type")).lower()
        if cfg.get("command") or typ == "stdio":
            transports.add("stdio")
            continue
        if typ == "sse":
            transports.add("sse")
            continue
        url = _text(cfg.get("urlTemplate") or cfg.get("url"))
        if not url:
            continue
        transports.add("http")
        auth = (setup.get(sname) or {}).get("auth") if isinstance(setup.get(sname), dict) else None
        header_fields: dict[str, tuple[str, str]] = {}
        if isinstance(auth, dict):
            if _text(auth.get("schema")):
                schema_refs.append(_text(auth.get("schema")))
            for header, spec in (auth.get("headers") or {}).items():
                if isinstance(spec, dict) and _text(spec.get("field")):
                    header_fields[str(header)] = (
                        _text(spec.get("field")),
                        str(spec.get("prefix") or ""),
                    )
        if _text(cfg.get("tokenSchema")):
            schema_refs.append(_text(cfg.get("tokenSchema")))
        headers = _dict(cfg.get("headers"))
        plans.append(
            ServerPlan(
                name=str(sname),
                url_template=url,
                headers={str(k): str(v) for k, v in headers.items()},
                header_fields=header_fields,
            )
        )

    fields: tuple[TokenField, ...] = ()
    form: dict[str, Any] = {}
    for ref in schema_refs or (["./ai.workbuddy/token-schema.json"] if plans else []):
        fields, form = _fields(_json(files, zf, _resolve(base, ref)))
        if fields:
            break

    needs_fields = any(
        p.header_fields
        or _PLACEHOLDER.search(p.url_template)
        or any(_PLACEHOLDER.search(v) for v in p.headers.values())
        for p in plans
    )
    auth = "token" if needs_fields else ("oauth" if mode in {"oauth", "mcp"} else "none")
    if plans:
        hosts = [urlparse(_PLACEHOLDER.sub("x", p.url_template)) for p in plans]
        if mode == "server-side":
            status = "server_side"
        elif mode == "oneid-token":
            status = "unsupported_auth"
        elif any(
            h.scheme != "https" or not h.hostname or _INTERNAL_HOST.search(h.hostname)
            for h in hosts
        ):
            status = "internal"
        elif needs_fields and not fields:
            status = "unsupported_auth"
        else:
            status = "supported"
    elif f"{base}ai.workbuddy/cli.json" in files:
        status = "cli"
    elif "stdio" in transports:
        status = "stdio"
    elif "sse" in transports:
        status = "sse"
    else:
        status = "no_mcp"

    skills = sorted(
        {
            rel[len(f"{base}skills/") :].split("/", 1)[0]
            for rel in files
            if rel.startswith(f"{base}skills/") and rel.endswith("/SKILL.md")
        }
    )
    icon_member = next(
        (
            rel
            for rel in sorted(files)
            if rel.startswith(f"{base}ai.workbuddy/assets/icon")
            and PurePosixPath(rel).suffix.lower() in _ICON_TYPES
        ),
        None,
    )
    return WorkBuddyConnector(
        id=cid,
        name=name or {"zh": cid},
        description=description,
        examples=examples,
        status=status,
        auth=auth,
        servers=tuple(plans),
        fields=fields,
        form=form,
        skills=tuple(skills),
        icon_member=icon_member,
    )


def _parse(zip_path: str, version: str) -> _Catalog:
    catalog = _Catalog(version=version, zip_path=zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        infos = {i.filename: i for i in _entries(zf)}
        catalog_rel = next(
            (n for n in infos if n.endswith(CATALOG_PATH) and n.count("/") == 1), None
        )
        if catalog_rel is None:
            raise SkillPackageError("WorkBuddy connector market has no catalog")
        root = catalog_rel[: -len(CATALOG_PATH)]
        raw = _json(infos, zf, catalog_rel)
        plugins = raw.get("plugins") if isinstance(raw, dict) else None
        for entry in plugins or []:
            if not isinstance(entry, dict):
                continue
            cid = _text(entry.get("name"))
            source = _text(entry.get("source"))
            if not _ID_RE.match(cid) or cid in catalog.connectors or not source:
                continue
            base = _resolve(root, source).rstrip("/") + "/"
            if ".." in PurePosixPath(base).parts:
                continue
            files = {n: i for n, i in infos.items() if n.startswith(base)}
            conn = _connector(cid, base, files, zf)
            if conn is None:
                continue
            catalog.connectors[cid] = conn
            catalog.order.append(cid)
    return catalog


def _load(force: bool = False) -> _Catalog:
    global _CACHE
    cached = fetch_cached(
        "connector-plugins.zip",
        market_url(CONNECTOR_MARKET_URL_ENV, CONNECTOR_MARKET_URL),
        force=force,
    )
    with _LOCK:
        if _CACHE is not None and _CACHE.version == cached.version and not force:
            return _CACHE
        _CACHE = _parse(str(cached.path), cached.version)
        return _CACHE


def list_connectors(
    query: str = "", *, supported_only: bool = False, force: bool = False
) -> tuple[list[WorkBuddyConnector], str]:
    catalog = _load(force=force)
    q = query.strip().lower()
    out: list[WorkBuddyConnector] = []
    for cid in catalog.order:
        conn = catalog.connectors[cid]
        if supported_only and conn.status != "supported":
            continue
        if q and q not in " ".join([cid, *conn.name.values(), *conn.description.values()]).lower():
            continue
        out.append(conn)
    out.sort(key=lambda c: c.status != "supported")
    return out, catalog.version


def get_connector(cid: str) -> WorkBuddyConnector:
    conn = _load().connectors.get(cid)
    if conn is None:
        raise WorkBuddyConnectorNotFound(cid)
    return conn


def connector_icon(cid: str) -> tuple[bytes, str]:
    catalog = _load()
    conn = catalog.connectors.get(cid)
    if conn is None or conn.icon_member is None:
        raise WorkBuddyConnectorNotFound(cid)
    media_type = _ICON_TYPES[PurePosixPath(conn.icon_member).suffix.lower()]
    cached = catalog.icon_cache.get(cid)
    if cached is None:
        with zipfile.ZipFile(catalog.zip_path) as zf:
            info = {i.filename: i for i in _entries(zf)}.get(conn.icon_member)
            if info is None:
                raise WorkBuddyConnectorNotFound(cid)
            cached = _read_member(zf, info, 4 * _SMALL)
        catalog.icon_cache[cid] = cached
    return cached, media_type


def _fill(template: str, values: dict[str, str], *, url: bool) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise WorkBuddyConnectorInputError(f"missing value for {key}")
        return quote(values[key], safe="") if url else values[key]

    return _PLACEHOLDER.sub(repl, template)


def build_specs(conn: WorkBuddyConnector, values: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Custom-MCP specs (server name -> spec) for ``conn`` with the user's values."""
    if conn.status != "supported":
        raise WorkBuddyConnectorInputError(f"connector is not installable here: {conn.status}")
    clean = {k: v.strip() for k, v in values.items() if isinstance(v, str) and v.strip()}
    missing = [f.key for f in conn.fields if f.required and f.key not in clean]
    if conn.auth == "token" and missing:
        raise WorkBuddyConnectorInputError(f"missing value for {', '.join(missing)}")
    label = conn.name.get("zh") or conn.name.get("en") or conn.id
    specs: dict[str, dict[str, Any]] = {}
    for plan in conn.servers:
        headers: dict[str, str] = {}
        for header, template in plan.headers.items():
            try:
                headers[header] = _fill(template, clean, url=False)
            except WorkBuddyConnectorInputError:
                continue  # optional field left empty: leave the header out
        for header, (field_key, prefix) in plan.header_fields.items():
            if field_key in clean:
                headers[header] = f"{prefix}{clean[field_key]}"
        spec: dict[str, Any] = {
            "transport": "streamable_http",
            "url": _fill(plan.url_template, clean, url=True),
            "enabled": True,
            "display_name": (label if len(conn.servers) == 1 else f"{label} · {plan.name}")[:64],
        }
        if headers:
            spec["headers"] = headers
        if conn.auth == "oauth":
            spec["oauth"] = {"required": True}
        specs[custom_mcp_name(plan.name if len(conn.servers) > 1 else conn.id)] = spec
    return specs


def install(
    svc: Any, user_id: int, conn: WorkBuddyConnector, values: dict[str, str], *, shared: bool
) -> list[str]:
    """Add (or update) ``conn``'s servers in the user's custom MCP document."""
    from octop.infra.connectors.service import ConnectorNameTakenError  # noqa: PLC0415

    specs = build_specs(conn, values)
    merged: dict[str, Any] = dict(svc.get_custom_servers(user_id))
    for name, spec in specs.items():
        old = merged.get(name)
        if shared:
            spec["shared"] = True
        if isinstance(old, dict) and old.get("default_open") is True:
            spec["default_open"] = True
        merged[name] = spec
    try:
        svc.put_custom_servers(user_id, merged)
    except ConnectorNameTakenError:
        for name in specs:
            merged[name]["display_name"] = f"{specs[name]['display_name']} (WorkBuddy)"[:64]
        svc.put_custom_servers(user_id, merged)
    return list(specs)


def installed_ids(servers: dict[str, Any]) -> set[str]:
    """Connector ids whose ``wb-<id>`` server exists in a custom MCP document."""
    return {name[len("wb-") :] for name in servers if name.startswith("wb-")}


def reset_cache_for_tests() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None
