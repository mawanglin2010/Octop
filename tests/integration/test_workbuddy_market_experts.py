"""fork: /api/workbuddy/experts — list, preview, prepare, then create an agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.workbuddy import fake_expert_market_fixture  # noqa: F401


async def test_list_and_preview(env_with_provider: Any, fake_expert_market: Path) -> None:
    client, _srv, auth = env_with_provider
    assert (await client.get("/api/workbuddy/experts")).status_code == 401
    r = await client.get("/api/workbuddy/experts", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["plugin"] for e in body["items"]] == ["form-expert"]
    assert body["items"][0]["template_id"] == "wb-form-expert"

    r = await client.get("/api/workbuddy/experts/form-expert", headers=auth)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["prompt"].startswith("# Form Expert")
    assert {m["name"]: m["status"] for m in detail["mcp"]}["forms"] == "ok"
    assert detail["connectors"] == ["kdocs"]
    assert (await client.get("/api/workbuddy/experts/fin-team", headers=auth)).status_code == 404


async def test_prepare_then_create_agent(env_with_provider: Any, fake_expert_market: Path) -> None:
    client, _srv, auth = env_with_provider
    r = await client.post("/api/workbuddy/experts/form-expert/prepare", headers=auth, json={})
    assert r.status_code == 200, r.text
    prepared = r.json()
    assert prepared["expert_id"] == "wb-form-expert"
    assert prepared["mcp_servers"] == ["wb-forms"]
    assert {s["name"] for s in prepared["skipped_mcp"]} == {
        "intranet",
        "local-tool",
        "legacy",
        "secret",
    }

    servers = (await client.get("/api/connectors/custom-mcp", headers=auth)).json()["servers"]
    assert servers["wb-forms"]["url"] == "https://forms.example.com/mcp"

    template = await client.get("/api/experts/wb-form-expert", headers=auth)
    assert template.status_code == 200, template.text
    assert template.json()["label"]["zh"] == "表单专家·表哥"

    created = await client.post(
        "/api/agents/from-expert/wb-form-expert",
        headers=auth,
        json={"name": "表单助手", "mcp_servers": prepared["mcp_servers"]},
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent["expert_id"] == "wb-form-expert"

    # Preparing again reuses the same custom MCP server.
    again = await client.post(
        "/api/workbuddy/experts/form-expert/prepare", headers=auth, json={"add_mcp": True}
    )
    assert again.json()["mcp_servers"] == ["wb-forms"]
    servers = (await client.get("/api/connectors/custom-mcp", headers=auth)).json()["servers"]
    assert list(servers) == ["wb-forms"]


async def test_prepare_without_mcp(env_with_provider: Any, fake_expert_market: Path) -> None:
    client, _srv, auth = env_with_provider
    r = await client.post(
        "/api/workbuddy/experts/form-expert/prepare", headers=auth, json={"add_mcp": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["mcp_servers"] == []
    servers = (await client.get("/api/connectors/custom-mcp", headers=auth)).json()["servers"]
    assert servers == {}
