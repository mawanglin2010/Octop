"""fork: fake WorkBuddy market archives for tests."""

from __future__ import annotations

import json
import stat
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

SKILL_MD = """---
name: {slug}
description: {desc}
---

# {slug}

Use this skill to {desc}.
"""


class _NoUtf8FlagInfo(zipfile.ZipInfo):
    """Writes the UTF-8 name bytes but leaves the UTF-8 flag (0x800) unset."""

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:  # noqa: N802
        return self.filename.encode("utf-8"), self.flag_bits & ~0x800


def build_skill_market_zip(path: Path) -> Path:
    catalog = {
        "name": "fake",
        "tags_zh": ["办公协同", "开发工具"],
        "tags_en": ["Collaboration", "Dev Tools"],
        "skills": [
            {
                "name": "文档助手",
                "name_en": "Doc Helper",
                "description": "Create and edit online documents.",
                "description_zh": "创建和编辑在线文档",
                "description_en": "Create and edit online docs",
                "source": "doc-helper",
                "version": "1.0.2",
                "tags_zh": ["办公协同"],
                "tags_en": ["Collaboration"],
                "examples_zh": ["新建一个文档"],
                "examples_en": ["Create a document"],
                "featured": True,
            },
            {
                "name": "代码检查",
                "description": "Lint code. Works with the WorkBuddy toolbox.",
                "description_zh": "检查代码",
                "source": "code-lint",
                "version": "0.1.0",
                "tags_zh": ["开发工具"],
                "tags_en": ["Dev Tools"],
            },
            # Listed but has no directory: skipped.
            {"name": "幽灵", "source": "ghost"},
            # Unsafe slug: skipped.
            {"name": "坏的", "source": "../evil"},
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(".codebuddy-skill/marketplace.json", json.dumps(catalog, ensure_ascii=False))
        zf.writestr(
            "skills/doc-helper/SKILL.md",
            SKILL_MD.format(slug="doc-helper", desc="edit docs"),
        )
        zf.writestr("skills/doc-helper/scripts/run.py", "print('hi')\n")
        # UTF-8 name stored without the UTF-8 flag, as WorkBuddy's zips do.
        zf.writestr(_NoUtf8FlagInfo("skills/doc-helper/references/说明.md"), "# 说明\n")
        # ...and one that was mangled through CP437 twice before being stored.
        twice = "skills/doc-helper/references/地图.html".encode().decode("cp437")
        zf.writestr(_NoUtf8FlagInfo(twice), "<p>map</p>")
        zf.writestr(
            "skills/code-lint/SKILL.md",
            SKILL_MD.format(slug="code-lint", desc="lint code via workbuddy_lint"),
        )
        zf.writestr("skills/code-lint/../../escape.txt", "nope")
        link = zipfile.ZipInfo("skills/code-lint/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
        zf.writestr("icons/doc-helper.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
        zf.writestr("icons/unrelated.txt", "x")
    return path


@pytest.fixture(name="fake_skill_market")
def fake_skill_market_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the WorkBuddy skill market at a local fake zip (no network)."""
    from octop.infra.workbuddy import archive, skills

    zip_path = build_skill_market_zip(tmp_path / "skill-market.zip")

    def fake_fetch(name: str, url: str, **_kw: object) -> archive.CachedFile:
        return archive.CachedFile(zip_path, "fake-v1", 0.0)

    monkeypatch.setattr(skills, "fetch_cached", fake_fetch)
    skills.reset_cache_for_tests()
    yield zip_path
    skills.reset_cache_for_tests()


EXPERT_CENTER = {
    "version": "1.0.0",
    "categories": [
        {"id": "04-DataAI", "name": {"zh": "数据智能", "en": "Data & AI"}},
        {"id": "08-FinanceInvestment", "name": {"zh": "金融投资", "en": "Finance"}},
    ],
    "experts": [
        {
            "id": "FormExpert",
            "plugin": "form-expert",
            "expertType": "agent",
            "categoryId": "04-DataAI",
            "displayName": {"zh": "表哥", "en": "Formy"},
            "profession": {"zh": "表单专家", "en": "Form Expert"},
            "description": {"zh": "帮你建表单", "en": "Builds forms"},
            "avatar": "/avatars/FormExpert.png",
            "tags": [{"zh": "表单", "en": "Forms"}],
            "quickPrompts": [{"zh": "建一个报名表", "en": "Make a sign-up form"}],
        },
        {
            "id": "FinTeam",
            "plugin": "fin-team",
            "expertType": "team",
            "categoryId": "08-FinanceInvestment",
            "profession": {"zh": "金融团队"},
        },
        {"id": "Bad", "plugin": "../evil", "expertType": "agent"},
    ],
}

FORM_PLUGIN_JSON = {
    "name": "form-expert",
    "version": "1.2.0",
    "expertType": "agent",
    "agentName": "form-expert",
    "agents": ["./agents/form-expert.md"],
    "skills": ["./skills/forms"],
    "displayName": {"zh": "表哥", "en": "Formy"},
    "profession": {"zh": "表单专家", "en": "Form Expert"},
    "displayDescription": {"zh": "用 MCP 建表单", "en": "Builds forms over MCP"},
    "quickPrompts": [{"zh": "建一个报名表", "en": "Make a sign-up form"}],
    "dependencies": {"mcpServers": "./.mcp.json", "connectors": ["kdocs"]},
}

FORM_MCP = {
    "mcpServers": {
        "forms": {"type": "http", "url": "https://forms.example.com/mcp"},
        "intranet": {"type": "http", "url": "https://hr.mcp.woa.com/x"},
        "local-tool": {"command": "node", "args": ["dist/server.js"]},
        "legacy": {"type": "sse", "url": "https://sse.example.com/sse"},
        "secret": {
            "type": "http",
            "url": "https://secret.example.com/mcp",
            "headers": {"Authorization": "Bearer ${user_config.TOKEN}"},
        },
    }
}


def build_expert_bundle(path: Path) -> Path:
    import io  # noqa: PLC0415
    import tarfile  # noqa: PLC0415

    def add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(path, "w:gz") as tar:
        add(tar, "./.codebuddy-plugin/plugin.json", json.dumps(FORM_PLUGIN_JSON).encode())
        add(tar, "./.mcp.json", json.dumps(FORM_MCP).encode())
        add(
            tar,
            "./agents/form-expert.md",
            b"---\nname: form-expert\n---\n\n# Form Expert\n\nYou build forms.\n",
        )
        add(tar, "./skills/forms/SKILL.md", b"---\nname: forms\ndescription: Make forms\n---\nx\n")
        add(tar, "./skills/forms/references/guide.md", b"guide")
        add(tar, "./license/forms.LICENSE", b"MIT")
        add(tar, "./README.md", b"readme")
        add(tar, "../escape.md", b"nope")
        link = tarfile.TarInfo("./skills/forms/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    return path


@pytest.fixture(name="fake_expert_market")
def fake_expert_market_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Serve a fake WorkBuddy expert center and one bundle (no network)."""
    from octop.infra.workbuddy import archive, experts

    center = tmp_path / "expert_center.json"
    center.write_text(json.dumps(EXPERT_CENTER, ensure_ascii=False), encoding="utf-8")
    bundle = build_expert_bundle(tmp_path / "form-expert.tar.gz")

    def fake_fetch(name: str, url: str, **_kw: object) -> archive.CachedFile:
        if name == "expert_center.json":
            return archive.CachedFile(center, "center-v1", 0.0)
        if name == "experts/form-expert.tar.gz":
            return archive.CachedFile(bundle, "bundle-v1", 0.0)
        raise archive.WorkBuddyFetchError(f"not faked: {name}")

    monkeypatch.setattr(experts, "fetch_cached", fake_fetch)
    experts.reset_cache_for_tests()
    yield tmp_path
    experts.reset_cache_for_tests()
