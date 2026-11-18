"""fork: WorkBuddy expert market — catalog, bundle reading, template, MCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.support.workbuddy import fake_expert_market_fixture  # noqa: F401

from octop.infra.workbuddy import experts as wb


def test_lists_single_agent_experts_only(fake_expert_market: Path) -> None:
    items, categories, version = wb.list_experts()
    assert [e.plugin for e in items] == ["form-expert"]  # team + unsafe plugin dropped
    assert version == "center-v1"
    assert categories[0] == {"id": "04-DataAI", "zh": "数据智能", "en": "Data & AI"}
    expert = items[0]
    assert expert.template_id == "wb-form-expert"
    assert expert.category == {"zh": "数据智能", "en": "Data & AI"}
    assert expert.avatar_url is not None
    assert expert.avatar_url.endswith("/avatars/FormExpert.png")
    assert wb.list_experts("表单")[0] == items
    assert wb.list_experts("", "08-FinanceInvestment")[0] == []
    with pytest.raises(wb.WorkBuddyExpertNotFound):
        wb.get_expert("fin-team")


@pytest.mark.parametrize(
    ("cfg", "status"),
    [
        ({"type": "http", "url": "https://a.example.com/mcp"}, "ok"),
        ({"type": "streamableHttp", "url": "https://a.example.com/mcp"}, "ok"),
        ({"type": "http", "url": "http://a.example.com/mcp"}, "internal"),
        ({"type": "http", "url": "https://x.mcp.woa.com/mcp"}, "internal"),
        ({"type": "http", "url": "https://lexiang.auth-proxy.local"}, "internal"),
        ({"type": "sse", "url": "https://a.example.com/sse"}, "sse"),
        ({"command": "npx", "args": ["-y", "pkg"]}, "stdio"),
        (
            {"type": "http", "url": "https://a.example.com", "headers": {"A": "${X}"}},
            "needs_secret",
        ),
        ({"type": "http"}, "invalid"),
        ("nope", "invalid"),
    ],
)
def test_classify_mcp(cfg: Any, status: str) -> None:
    assert wb.classify_mcp("s", cfg).status == status


def test_read_bundle_and_preview(fake_expert_market: Path) -> None:
    bundle = wb.read_bundle("form-expert")
    assert bundle.prompt == "# Form Expert\n\nYou build forms.\n"
    assert sorted(bundle.skills) == ["forms"]
    assert sorted(rel for rel, _ in bundle.skills["forms"]) == ["SKILL.md", "references/guide.md"]
    assert bundle.licenses == [("forms.LICENSE", b"MIT")]
    assert bundle.connectors == ["kdocs"]
    assert {m.name: m.status for m in bundle.mcp} == {
        "forms": "ok",
        "intranet": "internal",
        "local-tool": "stdio",
        "legacy": "sse",
        "secret": "needs_secret",
    }
    detail = wb.preview("form-expert")
    assert detail["skills"] == [{"name": "forms", "description": "Make forms", "file_count": 2}]
    assert detail["connectors"] == ["kdocs"]


def test_install_template(fake_expert_market: Path, tmp_path: Path) -> None:
    root = tmp_path / "expert_market"
    expert_id, _bundle = wb.install_template("form-expert", root)
    assert expert_id == "wb-form-expert"
    target = root / expert_id
    files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
    assert files == [
        "SOUL.md",
        "licenses/forms.LICENSE",
        "manifest.json",
        "skills/forms/SKILL.md",
        "skills/forms/references/guide.md",
    ]
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "wb-form-expert"
    assert manifest["label"] == {"zh": "表单专家·表哥", "en": "Form Expert·Formy"}
    assert manifest["scene"] == "tech"
    assert manifest["prompt_files"] == ["SOUL.md"]
    assert manifest["task_examples"] == {"zh": ["建一个报名表"], "en": ["Make a sign-up form"]}
    assert manifest["source"]["type"] == "workbuddy"
    # Re-installing replaces the directory.
    (target / "stale.txt").write_text("x")
    wb.install_template("form-expert", root)
    assert not (target / "stale.txt").exists()


class _FakeConnectorService:
    def __init__(self, existing: dict[str, Any], *, allowed: bool = True) -> None:
        self.servers = dict(existing)
        self.allowed = allowed
        self.puts = 0

    def get_custom_servers(self, user_id: int) -> dict[str, Any]:
        return dict(self.servers)

    def put_custom_servers(self, user_id: int, servers: dict[str, Any]) -> dict[str, Any]:
        self.puts += 1
        self.servers = dict(servers)
        return self.servers

    def list_active_mcp_server_names(self, user_id: int) -> list[str]:
        return sorted(self.servers) if self.allowed else []


def test_ensure_custom_mcp_adds_and_reuses(fake_expert_market: Path) -> None:
    entries = wb.read_bundle("form-expert").mcp
    svc = _FakeConnectorService({"mine": {"transport": "stdio", "command": "x"}})
    names, skipped, changed = wb.ensure_custom_mcp(svc, 1, entries)
    assert names == ["wb-forms"] and changed and svc.puts == 1
    assert svc.servers["wb-forms"] == {
        "transport": "streamable_http",
        "url": "https://forms.example.com/mcp",
        "enabled": True,
        "display_name": "forms (WorkBuddy)",
    }
    assert "mine" in svc.servers
    assert {s["name"]: s["reason"] for s in skipped} == {
        "intranet": "internal",
        "local-tool": "stdio",
        "legacy": "sse",
        "secret": "needs_secret",
    }
    # Same URL again: reused, no write.
    names, _skipped, changed = wb.ensure_custom_mcp(svc, 1, entries)
    assert names == ["wb-forms"] and not changed and svc.puts == 1


def test_ensure_custom_mcp_without_connector_feature(fake_expert_market: Path) -> None:
    entries = wb.read_bundle("form-expert").mcp
    svc = _FakeConnectorService({}, allowed=False)
    names, skipped, _changed = wb.ensure_custom_mcp(svc, 1, entries)
    assert names == []
    assert {"name": "wb-forms", "reason": "connectors_disabled"} in skipped


def test_custom_mcp_name() -> None:
    assert wb.custom_mcp_name("aim.newtouchauto.com") == "wb-aim-newtouchauto-com"
    assert wb.custom_mcp_name("自评MCP") == "wb-mcp"
    assert wb.custom_mcp_name("hr_data_service_v1") == "wb-hr_data_service_v1"
