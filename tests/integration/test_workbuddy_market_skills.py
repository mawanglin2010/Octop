"""fork: /api/workbuddy/skills — browse, preview and install."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.workbuddy import fake_skill_market_fixture  # noqa: F401


async def test_requires_auth(env_with_main_agent: Any, fake_skill_market: Path) -> None:
    client, _srv, _auth, aid = env_with_main_agent
    assert (await client.get("/api/workbuddy/skills")).status_code == 401
    r = await client.post(
        f"/api/workbuddy/skills/install/agents/{aid}", json={"slug": "doc-helper"}
    )
    assert r.status_code == 401


async def test_list_detail_and_icon(env_with_main_agent: Any, fake_skill_market: Path) -> None:
    client, _srv, auth, _aid = env_with_main_agent
    r = await client.get("/api/workbuddy/skills", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "fake-v1"
    assert [s["slug"] for s in body["items"]] == ["doc-helper", "code-lint"]
    assert body["items"][0]["icon_url"] == "/api/workbuddy/skills/doc-helper/icon"
    assert body["items"][1]["icon_url"] is None
    assert body["items"][1]["mentions_workbuddy"] is True

    r = await client.get("/api/workbuddy/skills", headers=auth, params={"category": "开发工具"})
    assert [s["slug"] for s in r.json()["items"]] == ["code-lint"]

    r = await client.get("/api/workbuddy/skills/doc-helper", headers=auth)
    assert r.status_code == 200, r.text
    assert [f["path"] for f in r.json()["files"]] == [
        "SKILL.md",
        "references/地图.html",
        "references/说明.md",
        "scripts/run.py",
    ]

    r = await client.get("/api/workbuddy/skills/doc-helper/icon", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in r.headers["content-security-policy"]

    assert (await client.get("/api/workbuddy/skills/ghost", headers=auth)).status_code == 404


async def test_install_into_agent(env_with_agent: Any, fake_skill_market: Path) -> None:
    client, _srv, auth, aid = env_with_agent
    r = await client.post(
        f"/api/workbuddy/skills/install/agents/{aid}",
        headers=auth,
        json={"slug": "doc-helper"},
    )
    assert r.status_code == 201, r.text
    assert r.json() == {"installed": True, "name": "doc-helper"}

    detail = await client.get(f"/api/agents/{aid}/skills/doc-helper", headers=auth)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["frontmatter"]["metadata"]["octop"]["source"] == "workbuddy"
    assert payload["label"] == {"zh": "文档助手", "en": "Doc Helper"}

    r = await client.post(
        f"/api/workbuddy/skills/install/agents/{aid}",
        headers=auth,
        json={"slug": "ghost"},
    )
    assert r.status_code == 404


async def test_install_into_package(env_with_main_agent: Any, fake_skill_market: Path) -> None:
    client, _srv, auth, _aid = env_with_main_agent
    created = await client.post(
        "/api/skill-packages", headers=auth, json={"name": "WorkBuddy 测试包"}
    )
    assert created.status_code == 200, created.text
    package_id = created.json()["id"]
    r = await client.post(
        f"/api/workbuddy/skills/install/packages/{package_id}",
        headers=auth,
        json={"slug": "code-lint"},
    )
    assert r.status_code == 201, r.text
    skills = await client.get(f"/api/skill-packages/{package_id}/skills", headers=auth)
    assert [s["slug"] for s in skills.json()] == ["code-lint"]

    r = await client.post(
        f"/api/workbuddy/skills/install/packages/{package_id}",
        headers=auth,
        json={"slug": "code-lint", "overwrite": False},
    )
    assert r.status_code == 409
