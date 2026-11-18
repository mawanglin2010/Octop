"""Download and cache WorkBuddy market files (fork).

Each market file is kept next to a small JSON sidecar holding the validators
(``ETag`` / ``Last-Modified``) and the time it was last checked. Within the TTL
the cached copy is used as is; after that a conditional GET either confirms it
(304) or replaces it. When the CDN cannot be reached a stale copy still serves.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from octop.infra.utils.paths import PathLayout

logger = logging.getLogger(__name__)

SKILL_MARKET_URL = "https://download.codebuddy.cn/skill-marketplace/skill-marketplace.zip"
CONNECTOR_MARKET_URL = "https://static.workbuddy.cn/connector-agent-plugins/connector-plugins.zip"
EXPERT_MARKET_BASE = (
    "https://acc-1258344699.cos.accelerate.myqcloud.com/workbuddy/expert-marketplace"
)

SKILL_MARKET_URL_ENV = "OCTOP_WORKBUDDY_SKILL_MARKET_URL"
CONNECTOR_MARKET_URL_ENV = "OCTOP_WORKBUDDY_CONNECTOR_MARKET_URL"
EXPERT_MARKET_BASE_ENV = "OCTOP_WORKBUDDY_EXPERT_MARKET_BASE"

DEFAULT_TTL_SECONDS = 6 * 3600
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_CHUNK = 256 * 1024
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class WorkBuddyFetchError(RuntimeError):
    """The market file could not be fetched and no cached copy exists."""


@dataclass(frozen=True)
class CachedFile:
    path: Path
    version: str  # sha256 prefix of the cached content
    checked_at: float


def market_url(env_name: str, default: str) -> str:
    """Configured URL (env override) — https only."""
    raw = os.environ.get(env_name, "").strip() or default
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WorkBuddyFetchError(f"{env_name} must be an https URL")
    return raw


def cache_root() -> Path:
    return PathLayout.from_env().root / "workbuddy_market"


def _lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_meta(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def fetch_cached(
    name: str,
    url: str,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    force: bool = False,
) -> CachedFile:
    """Return a local copy of ``url`` cached as ``name`` (blocking; run in a thread)."""
    root = cache_root()
    target = root / name
    meta_path = root / f"{name}.meta.json"
    with _lock(name):
        meta = _read_meta(meta_path)
        have = target.is_file() and meta.get("url") == url
        now = time.time()
        raw_checked = meta.get("checked_at")
        checked_at = float(raw_checked) if isinstance(raw_checked, int | float) else 0.0
        if have and not force and now - checked_at < ttl:
            return CachedFile(target, str(meta.get("version") or ""), checked_at)

        headers = {"User-Agent": "Octop"}
        if have:
            if meta.get("etag"):
                headers["If-None-Match"] = str(meta["etag"])
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = str(meta["last_modified"])
        try:
            meta = _download(url, target, meta, headers, max_bytes)
        except (httpx.HTTPError, OSError, WorkBuddyFetchError) as exc:
            if have:
                logger.warning("WorkBuddy market %s refresh failed, using cache: %s", name, exc)
                return CachedFile(target, str(meta.get("version") or ""), checked_at)
            raise WorkBuddyFetchError(f"failed to fetch {url}: {exc}") from exc
        meta["url"] = url
        meta["checked_at"] = now
        root.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return CachedFile(target, str(meta.get("version") or ""), now)


def _download(
    url: str,
    target: Path,
    meta: dict[str, object],
    headers: dict[str, str],
    max_bytes: int,
) -> dict[str, object]:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with (
        httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client,
        client.stream("GET", url, headers=headers) as resp,
    ):
        if resp.status_code == 304:
            return meta
        if resp.status_code != 200:
            raise WorkBuddyFetchError(f"HTTP {resp.status_code}")
        declared = int(resp.headers.get("content-length") or 0)
        if declared > max_bytes:
            raise WorkBuddyFetchError("market file is too large")
        size = 0
        try:
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise WorkBuddyFetchError("market file is too large")
                    fh.write(chunk)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return {
            "etag": resp.headers.get("etag", ""),
            "last_modified": resp.headers.get("last-modified", ""),
            "version": _sha256(target)[:16],
            "size": size,
        }
