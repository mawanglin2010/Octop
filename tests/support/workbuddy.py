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
