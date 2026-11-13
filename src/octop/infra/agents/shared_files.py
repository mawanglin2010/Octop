"""Per-user shared folder tools (fork): hand files between one user's experts.

Each expert has its own workspace and cannot see another expert's files, and team members only
exchange text. These tools give every expert of the same user one shared folder on the Octop host
(``{OCTOP_HOME}/users/<username>/shared/``): an expert copies a workspace file in with
``shared_file_put`` and another expert copies it into its own workspace with ``shared_file_get``.

Workspace reads and writes go through the agent's ``BackendWorkspace`` (local, docker or remote
backends alike); only the shared folder itself is a plain host directory. Files in it are never
cleaned up automatically; ``shared_file_delete`` removes one when the user asks for it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from octop.infra.utils.paths import PathLayout

SHARED_FILE_TOOLS: tuple[str, ...] = (
    "shared_file_put",
    "shared_file_get",
    "shared_file_list",
    "shared_file_delete",
)
SHARED_DIR_NAME = "shared"
MAX_SHARED_FILE_BYTES = 100 * 1024 * 1024
MAX_LIST_ENTRIES = 500
_NAME_MAX_LEN = 200
_BAD_CHARS = re.compile(r"[\x00-\x1f\\:*?\"<>|]")

_PUT_DESC = (
    "Copy a file from your workspace into the user's shared folder so the user's other experts "
    "can fetch it (e.g. generated images, PDFs, data files). Expert workspaces are isolated from "
    "each other; this folder is the only way to hand a file to another expert. Tell the receiving "
    "expert the shared name. The folder is shared by all experts of the same user and is not "
    "cleaned up automatically."
)
_GET_DESC = (
    "Copy a file from the user's shared folder into your workspace. Use the shared name another "
    "expert gave you (see shared_file_list). Returns the workspace path it was written to."
)
_LIST_DESC = "List files in the user's shared folder (name, size in bytes, modified time UTC)."
_DELETE_DESC = (
    "Delete one file from the user's shared folder. Only do this when the user explicitly asks; "
    "other experts may still need the file."
)


class SharedFileError(ValueError):
    """Bad shared-file request (reported back to the model as a tool error)."""


def shared_dir_for(layout: PathLayout, username: str) -> Path:
    """Host directory of *username*'s shared folder."""
    return layout.user_dir(username) / SHARED_DIR_NAME


def normalize_shared_name(raw: str) -> str:
    """Validate a shared name: relative POSIX path, no traversal, no odd characters."""
    name = str(raw or "").strip().replace("\\", "/")
    if not name:
        raise SharedFileError("shared name is empty")
    if len(name) > _NAME_MAX_LEN:
        raise SharedFileError(f"shared name is longer than {_NAME_MAX_LEN} characters")
    if name.startswith("/") or name.startswith("~"):
        raise SharedFileError("shared name must be relative (e.g. 'topic-a/cover.jpg')")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise SharedFileError("shared name must not contain '..'")
    if any(_BAD_CHARS.search(p) for p in parts):
        raise SharedFileError("shared name contains characters that are not allowed")
    return "/".join(parts)


def _target(root: Path, name: str) -> Path:
    path = (root / normalize_shared_name(name)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise SharedFileError("shared name escapes the shared folder") from None
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_file(root: Path, name: str, data: bytes, *, overwrite: bool) -> Path:
    path = _target(root, name)
    if path.exists() and not overwrite:
        raise SharedFileError(f"'{name}' already exists in the shared folder; pass overwrite=true")
    if path.is_dir():
        raise SharedFileError(f"'{name}' is a folder")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def _read_file(root: Path, name: str) -> bytes:
    path = _target(root, name)
    if not path.is_file():
        raise SharedFileError(f"'{name}' is not in the shared folder (see shared_file_list)")
    if path.stat().st_size > MAX_SHARED_FILE_BYTES:
        raise SharedFileError("file is too large")
    return path.read_bytes()


def _list_files(root: Path, prefix: str) -> dict[str, Any]:
    if not root.is_dir():
        return {"files": [], "truncated": False}
    base = _target(root, prefix) if prefix.strip() else root.resolve()
    rows: list[dict[str, Any]] = []
    truncated = False
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if len(rows) >= MAX_LIST_ENTRIES:
                truncated = True
                break
            st = path.stat()
            rows.append(
                {
                    "name": path.relative_to(root.resolve()).as_posix(),
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(
                        timespec="seconds"
                    ),
                }
            )
    return {"files": rows, "truncated": truncated}


def _delete_file(root: Path, name: str) -> None:
    path = _target(root, name)
    if path.is_dir():
        if any(path.iterdir()):
            raise SharedFileError(f"'{name}' is a non-empty folder; delete its files first")
        path.rmdir()
        return
    if not path.is_file():
        raise SharedFileError(f"'{name}' is not in the shared folder")
    path.unlink()
    parent = path.parent
    real_root = root.resolve()
    while parent != real_root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(exc: Exception) -> str:
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


def build_shared_file_tools(
    *,
    layout: PathLayout,
    username_for: Callable[[dict[str, Any]], str | None],
    workspace_for: Callable[[str], Any],
) -> list[StructuredTool]:
    """Shared-folder tools.

    ``username_for(configurable)`` returns the username that owns the current turn (or ``None``),
    ``workspace_for(agent_id)`` the agent's harness ``BackendWorkspace``.
    """

    def _ctx() -> tuple[Path, Any]:
        cfg = get_config().get("configurable") or {}
        username = username_for(cfg)
        if not username:
            raise SharedFileError("no user for this turn; the shared folder is per user")
        agent_id = str(cfg.get("agent_id") or "").strip()
        workspace = workspace_for(agent_id) if agent_id else None
        if workspace is None:
            raise SharedFileError("agent workspace is not available")
        return shared_dir_for(layout, username), workspace

    async def shared_file_put(
        path: Annotated[str, Field(description="Workspace path of the file to share.")],
        name: Annotated[
            str | None,
            Field(
                description=(
                    "Name in the shared folder, may include sub folders (e.g. "
                    "'hot-topic-a/cover.jpg'). Defaults to the file name."
                )
            ),
        ] = None,
        overwrite: Annotated[
            bool, Field(description="Replace an existing shared file with the same name.")
        ] = False,
    ) -> str:
        try:
            root, workspace = _ctx()
            data = await workspace.adownload_bytes(path)
            if data is None:
                raise SharedFileError(f"'{path}' was not found in your workspace")
            if len(data) > MAX_SHARED_FILE_BYTES:
                raise SharedFileError(
                    f"file is larger than {MAX_SHARED_FILE_BYTES // (1024 * 1024)} MB"
                )
            target = normalize_shared_name(name or PurePosixPath(path.replace("\\", "/")).name)
            await asyncio.to_thread(_write_file, root, target, data, overwrite=overwrite)
            return _ok({"shared_name": target, "size": len(data), "sha256": _sha256(data)})
        except Exception as exc:
            return _err(exc)

    async def shared_file_get(
        name: Annotated[str, Field(description="Name in the shared folder.")],
        path: Annotated[
            str | None,
            Field(
                description="Workspace path to write to. Defaults to 'shared/<name>'.",
            ),
        ] = None,
    ) -> str:
        try:
            root, workspace = _ctx()
            clean = normalize_shared_name(name)
            data = await asyncio.to_thread(_read_file, root, clean)
            dest = (path or f"{SHARED_DIR_NAME}/{clean}").strip()
            await workspace.aupload_bytes(dest, data)
            return _ok({"path": dest, "size": len(data), "sha256": _sha256(data)})
        except Exception as exc:
            return _err(exc)

    async def shared_file_list(
        prefix: Annotated[
            str, Field(description="Only list files under this sub folder (optional).")
        ] = "",
    ) -> str:
        try:
            root, _workspace = _ctx()
            return _ok(await asyncio.to_thread(_list_files, root, prefix))
        except Exception as exc:
            return _err(exc)

    async def shared_file_delete(
        name: Annotated[str, Field(description="Name in the shared folder to delete.")],
    ) -> str:
        try:
            root, _workspace = _ctx()
            await asyncio.to_thread(_delete_file, root, name)
            return _ok({"deleted": normalize_shared_name(name)})
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=shared_file_put, name="shared_file_put", description=_PUT_DESC
        ),
        StructuredTool.from_function(
            coroutine=shared_file_get, name="shared_file_get", description=_GET_DESC
        ),
        StructuredTool.from_function(
            coroutine=shared_file_list, name="shared_file_list", description=_LIST_DESC
        ),
        StructuredTool.from_function(
            coroutine=shared_file_delete, name="shared_file_delete", description=_DELETE_DESC
        ),
    ]
