"""fork: /api/workbuddy/connectors — admin-only browse and install."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.workbuddy import fake_connector_market_fixture  # noqa: F401


async def test_admin_only(env_admin_alice: Any, fake_connector_market: Path) -> None:
    client, _srv, _admin, alice = env_admin_alice
    assert (await client.get("/api/workbuddy/connectors", headers=alice)).status_code == 403
    r = await client.post(
        "/api/workbuddy/connectors/open/install", headers=alice, json={"values": {}}
    )
    assert r.status_code == 403


async def test_list_detail_install(env_admin_alice: Any, fake_connector_market: Path) -> None:
    client, srv, admin, alice = env_admin_alice
    r = await client.get(
        "/api/workbuddy/connectors", headers=admin, params={"supported_only": True}
    )
    assert r.status_code == 200, r.text
    items = {c["id"]: c for c in r.json()["items"]}
    assert set(items) == {"keyed", "templated", "oauthy", "open"}
    assert items["keyed"]["icon_url"] == "/api/workbuddy/connectors/keyed/icon"
    assert items["keyed"]["installed"] is False

    detail = (await client.get("/api/workbuddy/connectors/keyed", headers=admin)).json()
    assert [f["key"] for f in detail["fields"]] == ["API_KEY", "REGION"]
    assert detail["skills"] == ["use-keyed"]

    missing = await client.post(
        "/api/workbuddy/connectors/keyed/install", headers=admin, json={"values": {}}
    )
    assert missing.status_code == 400
    blocked = await client.post(
        "/api/workbuddy/connectors/proxied/install", headers=admin, json={"values": {}}
    )
    assert blocked.status_code == 400

    ok = await client.post(
        "/api/workbuddy/connectors/keyed/install",
        headers=admin,
        json={"values": {"API_KEY": "sk-1"}, "shared": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"servers": ["wb-keyed"], "auth": "token"}
    oauth = await client.post(
        "/api/workbuddy/connectors/oauthy/install", headers=admin, json={"values": {}}
    )
    assert oauth.json()["auth"] == "oauth"

    servers = (await client.get("/api/connectors/custom-mcp", headers=admin)).json()["servers"]
    assert servers["wb-keyed"]["url"] == "https://k.example.com/mcp"
    assert servers["wb-keyed"]["shared"] is True
    assert "wb-oauthy" in servers

    listed = (await client.get("/api/workbuddy/connectors", headers=admin)).json()["items"]
    assert {c["id"] for c in listed if c["installed"]} == {"keyed", "oauthy"}

    # Alice cannot install, but the shared server is available to her agents.
    from octop.api.routers.connectors import _connector_service

    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    names = _connector_service(srv).list_active_mcp_server_names(alice_id)
    assert any(n.endswith("wb-keyed") for n in names), names
    assert not any(n.endswith("wb-oauthy") for n in names)  # not shared
