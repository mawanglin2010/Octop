"""fork: WorkBuddy connector market — classification, specs, install."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support.workbuddy import fake_connector_market_fixture  # noqa: F401

from octop.infra.workbuddy import connectors as wb


def test_classification(fake_connector_market: Path) -> None:
    items, version = wb.list_connectors()
    assert version == "conn-v1"
    got = {c.id: (c.status, c.auth) for c in items}
    assert got == {
        "keyed": ("supported", "token"),
        "templated": ("supported", "token"),
        "oauthy": ("supported", "oauth"),
        "open": ("supported", "none"),
        "proxied": ("server_side", "none"),
        "clitool": ("cli", "none"),
        "local": ("stdio", "none"),
        "intra": ("internal", "none"),
    }
    # Installable first; filter and search.
    assert [c.status for c in items][:4] == ["supported"] * 4
    assert {c.id for c in wb.list_connectors(supported_only=True)[0]} == {
        "keyed",
        "templated",
        "oauthy",
        "open",
    }
    assert [c.id for c in wb.list_connectors("免配置")[0]] == ["open"]


def test_detail_fields_icon_skills(fake_connector_market: Path) -> None:
    conn = wb.get_connector("keyed")
    assert conn.name == {"zh": "带密钥", "en": "带密钥 EN"}
    assert [(f.key, f.secret, f.required) for f in conn.fields] == [
        ("API_KEY", True, True),
        ("REGION", False, False),
    ]
    assert conn.form["doc_url"] == "https://example.com/keys"
    assert conn.skills == ("use-keyed",)
    data, media_type = wb.connector_icon("keyed")
    assert media_type == "image/svg+xml" and data == b"<svg/>"
    with pytest.raises(wb.WorkBuddyConnectorNotFound):
        wb.connector_icon("open")
    with pytest.raises(wb.WorkBuddyConnectorNotFound):
        wb.get_connector("../evil")


def test_build_specs(fake_connector_market: Path) -> None:
    keyed = wb.build_specs(wb.get_connector("keyed"), {"API_KEY": " sk-1 ", "REGION": "cn"})
    assert keyed == {
        "wb-keyed": {
            "transport": "streamable_http",
            "url": "https://k.example.com/mcp",
            "enabled": True,
            "display_name": "带密钥",
            "headers": {"X-Region": "cn", "Authorization": "Bearer sk-1"},
        }
    }
    no_region = wb.build_specs(wb.get_connector("keyed"), {"API_KEY": "sk-1"})["wb-keyed"]
    assert no_region["headers"] == {"Authorization": "Bearer sk-1"}  # optional left out
    templated = wb.build_specs(wb.get_connector("templated"), {"API_KEY": "a b&c"})
    assert templated["wb-templated"]["url"] == "https://t.example.com/mcp?ak=a%20b%26c"
    oauthy = wb.build_specs(wb.get_connector("oauthy"), {})
    assert oauthy["wb-oauthy"]["oauth"] == {"required": True}
    assert "headers" not in wb.build_specs(wb.get_connector("open"), {})["wb-open"]

    with pytest.raises(wb.WorkBuddyConnectorInputError, match="API_KEY"):
        wb.build_specs(wb.get_connector("keyed"), {})
    with pytest.raises(wb.WorkBuddyConnectorInputError, match="server_side"):
        wb.build_specs(wb.get_connector("proxied"), {})


class _FakeSvc:
    def __init__(self, servers: dict[str, Any]) -> None:
        self.servers = servers

    def get_custom_servers(self, user_id: int) -> dict[str, Any]:
        return dict(self.servers)

    def put_custom_servers(self, user_id: int, servers: dict[str, Any]) -> dict[str, Any]:
        self.servers = dict(servers)
        return self.servers


def test_install_merges_and_updates(fake_connector_market: Path) -> None:
    svc = _FakeSvc({"mine": {"transport": "stdio", "command": "x"}})
    names = wb.install(svc, 1, wb.get_connector("keyed"), {"API_KEY": "k1"}, shared=True)
    assert names == ["wb-keyed"]
    assert svc.servers["wb-keyed"]["shared"] is True
    assert "mine" in svc.servers
    svc.servers["wb-keyed"]["default_open"] = True
    wb.install(svc, 1, wb.get_connector("keyed"), {"API_KEY": "k2"}, shared=False)
    spec = svc.servers["wb-keyed"]
    assert spec["headers"]["Authorization"] == "Bearer k2"
    assert spec["default_open"] is True and "shared" not in spec
    assert wb.installed_ids(svc.servers) == {"keyed"}
