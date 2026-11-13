"""Fork per-user shared folder tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octop.infra.agents import shared_files
from octop.infra.agents.settings.tool_catalog import BUILTIN_TOOL_CATALOG
from octop.infra.agents.shared_files import (
    SHARED_FILE_TOOLS,
    SharedFileError,
    build_shared_file_tools,
    normalize_shared_name,
    shared_dir_for,
)
from octop.infra.utils.paths import PathLayout


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def adownload_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)

    async def aupload_bytes(self, path: str, data: bytes) -> None:
        self.files[path] = data


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    layout = PathLayout(tmp_path)
    workspaces = {"illustrator": FakeWorkspace(), "publisher": FakeWorkspace()}
    owners = {"illustrator": "alice", "publisher": "alice"}
    state: dict[str, Any] = {"cfg": {}}
    monkeypatch.setattr(shared_files, "get_config", lambda: {"configurable": state["cfg"]})
    tools = {
        t.name: t
        for t in build_shared_file_tools(
            layout=layout,
            username_for=lambda cfg: cfg.get("username") or owners.get(cfg.get("agent_id", "")),
            workspace_for=workspaces.get,
        )
    }
    return {"layout": layout, "ws": workspaces, "tools": tools, "state": state}


async def _call(env: dict[str, Any], agent: str, tool: str, **kwargs: Any) -> dict[str, Any]:
    env["state"]["cfg"] = {"agent_id": agent, **kwargs.pop("_cfg", {})}
    return json.loads(await env["tools"][tool].coroutine(**kwargs))


@pytest.mark.asyncio
async def test_put_then_get_moves_bytes_between_experts(env: dict[str, Any]) -> None:
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    env["ws"]["illustrator"].files["generated/images/a.png"] = png

    put = await _call(env, "illustrator", "shared_file_put", path="generated/images/a.png",
                      name="hot-a/cover.png")  # fmt: skip
    assert put["shared_name"] == "hot-a/cover.png" and put["size"] == len(png)
    host = shared_dir_for(env["layout"], "alice") / "hot-a" / "cover.png"
    assert host.read_bytes() == png

    listed = await _call(env, "publisher", "shared_file_list")
    assert [f["name"] for f in listed["files"]] == ["hot-a/cover.png"]

    got = await _call(env, "publisher", "shared_file_get", name="hot-a/cover.png")
    assert got == {"path": "shared/hot-a/cover.png", "size": len(png), "sha256": put["sha256"]}
    assert env["ws"]["publisher"].files["shared/hot-a/cover.png"] == png


@pytest.mark.asyncio
async def test_put_refuses_overwrite_unless_asked(env: dict[str, Any]) -> None:
    env["ws"]["illustrator"].files["x.txt"] = b"one"
    assert "shared_name" in await _call(env, "illustrator", "shared_file_put", path="x.txt")
    env["ws"]["illustrator"].files["x.txt"] = b"two"
    again = await _call(env, "illustrator", "shared_file_put", path="x.txt")
    assert "already exists" in again["error"]
    forced = await _call(env, "illustrator", "shared_file_put", path="x.txt", overwrite=True)
    assert forced["size"] == 3
    assert (shared_dir_for(env["layout"], "alice") / "x.txt").read_bytes() == b"two"


@pytest.mark.asyncio
async def test_folders_are_per_user(env: dict[str, Any]) -> None:
    env["ws"]["illustrator"].files["x.txt"] = b"secret"
    await _call(env, "illustrator", "shared_file_put", path="x.txt")
    other = await _call(env, "publisher", "shared_file_list", _cfg={"username": "bob"})
    assert other == {"files": [], "truncated": False}
    missing = await _call(env, "publisher", "shared_file_get", name="x.txt",
                          _cfg={"username": "bob"})  # fmt: skip
    assert "not in the shared folder" in missing["error"]


@pytest.mark.asyncio
async def test_delete_removes_file_and_empty_parent(env: dict[str, Any]) -> None:
    env["ws"]["illustrator"].files["x.txt"] = b"x"
    await _call(env, "illustrator", "shared_file_put", path="x.txt", name="t/x.txt")
    assert await _call(env, "publisher", "shared_file_delete", name="t/x.txt") == {
        "deleted": "t/x.txt"
    }
    root = shared_dir_for(env["layout"], "alice")
    assert root.is_dir() and not (root / "t").exists()
    assert "error" in await _call(env, "publisher", "shared_file_delete", name="t/x.txt")


@pytest.mark.asyncio
async def test_errors_are_reported_not_raised(env: dict[str, Any]) -> None:
    missing = await _call(env, "illustrator", "shared_file_put", path="nope.png")
    assert "not found in your workspace" in missing["error"]
    escape = await _call(env, "illustrator", "shared_file_get", name="../../octop.db")
    assert "'..'" in escape["error"]
    nobody = await _call(env, "ghost", "shared_file_list")
    assert "no user" in nobody["error"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a.png", "a.png"),
        ("./t//b.jpg", "t/b.jpg"),
        ("t\\c.pdf", "t/c.pdf"),
        (" 热点/封面.jpg ", "热点/封面.jpg"),
    ],
)
def test_normalize_shared_name_accepts(raw: str, expected: str) -> None:
    assert normalize_shared_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "/etc/passwd", "~/x", "a/../b", "a:b", "a\x00b", "x" * 201])
def test_normalize_shared_name_rejects(raw: str) -> None:
    with pytest.raises(SharedFileError):
        normalize_shared_name(raw)


def test_tools_are_in_catalog_under_shared_category() -> None:
    entries = {e.name: e.category for e in BUILTIN_TOOL_CATALOG}
    assert {name: entries.get(name) for name in SHARED_FILE_TOOLS} == dict.fromkeys(
        SHARED_FILE_TOOLS, "shared"
    )
