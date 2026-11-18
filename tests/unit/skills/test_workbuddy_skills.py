"""fork: WorkBuddy skill market catalog / preview / install package."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from tests.support.workbuddy import fake_skill_market_fixture  # noqa: F401

from octop.infra.workbuddy import archive
from octop.infra.workbuddy import skills as wb


def test_catalog_lists_valid_skills(fake_skill_market: Path) -> None:
    items, categories, version = wb.list_skills()
    assert [s.slug for s in items] == ["doc-helper", "code-lint"]
    assert version == "fake-v1"
    assert categories == [
        {"zh": "办公协同", "en": "Collaboration"},
        {"zh": "开发工具", "en": "Dev Tools"},
    ]
    doc = items[0]
    assert doc.name == {"zh": "文档助手", "en": "Doc Helper"}
    assert doc.summary == {"zh": "创建和编辑在线文档", "en": "Create and edit online docs"}
    assert doc.category == {"zh": "办公协同", "en": "Collaboration"}
    assert doc.featured and doc.has_icon and not doc.mentions_workbuddy
    assert doc.file_count == 4
    lint = items[1]
    assert lint.mentions_workbuddy  # SKILL.md mentions workbuddy_lint
    assert not lint.has_icon
    assert lint.file_count == 1  # traversal entry and symlink skipped


def test_search_and_category(fake_skill_market: Path) -> None:
    assert [s.slug for s in wb.list_skills("文档")[0]] == ["doc-helper"]
    assert [s.slug for s in wb.list_skills("LINT")[0]] == ["code-lint"]
    assert [s.slug for s in wb.list_skills("", "Dev Tools")[0]] == ["code-lint"]
    assert wb.list_skills("", "不存在")[0] == []


def test_preview_and_icon(fake_skill_market: Path) -> None:
    preview = wb.skill_preview("doc-helper")
    assert preview["skill_md"].startswith("---\nname: doc-helper")
    assert [f["path"] for f in preview["files"]] == [
        "SKILL.md",
        "references/地图.html",
        "references/说明.md",
        "scripts/run.py",
    ]
    data, media_type = wb.skill_icon("doc-helper")
    assert media_type == "image/svg+xml" and data.startswith(b"<svg")
    with pytest.raises(wb.WorkBuddySkillNotFound):
        wb.skill_icon("code-lint")
    with pytest.raises(wb.WorkBuddySkillNotFound):
        wb.skill_preview("ghost")


def test_install_package_records_origin(fake_skill_market: Path) -> None:
    package = wb.resolve_install_package("doc-helper")
    assert package.slug == "doc-helper"
    assert package.source == "workbuddy"
    files = dict(package.files)
    assert set(files) == {
        "SKILL.md",
        "references/地图.html",
        "references/说明.md",
        "scripts/run.py",
    }
    text = files["SKILL.md"].decode()
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == "doc-helper"
    assert meta["metadata"]["octop"] == {
        "source": "workbuddy",
        "label": {"zh": "文档助手", "en": "Doc Helper"},
        "summary": {"zh": "创建和编辑在线文档", "en": "Create and edit online docs"},
    }
    assert "Use this skill to edit docs." in text


def test_install_package_size_limit(
    fake_skill_market: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wb, "MAX_SKILL_BYTES", 10)
    with pytest.raises(wb.SkillPackageTooLarge):
        wb.skill_files("doc-helper")


def test_fetch_cached_ttl_and_stale_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    calls: list[dict[str, str]] = []

    def fake_download(url, target, meta, headers, max_bytes):  # type: ignore[no-untyped-def]
        calls.append(headers)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"zip-bytes")
        return {"etag": '"e1"', "last_modified": "", "version": "v1", "size": 9}

    monkeypatch.setattr(archive, "_download", fake_download)
    first = archive.fetch_cached("m.zip", "https://example.com/m.zip")
    assert first.path == tmp_path / "workbuddy_market" / "m.zip"
    assert first.version == "v1"
    # Within the TTL: no request.
    archive.fetch_cached("m.zip", "https://example.com/m.zip")
    assert len(calls) == 1

    # After the TTL: conditional request with the stored validator.
    meta_path = tmp_path / "workbuddy_market" / "m.zip.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["checked_at"] = time.time() - archive.DEFAULT_TTL_SECONDS - 1
    meta_path.write_text(json.dumps(meta))

    def failing_download(*_a, **_k):  # type: ignore[no-untyped-def]
        raise archive.WorkBuddyFetchError("offline")

    monkeypatch.setattr(archive, "_download", failing_download)
    stale = archive.fetch_cached("m.zip", "https://example.com/m.zip")
    assert stale.version == "v1"  # offline: the cached copy still serves

    with pytest.raises(archive.WorkBuddyFetchError):
        archive.fetch_cached("other.zip", "https://example.com/other.zip")


def test_market_url_requires_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(archive.SKILL_MARKET_URL_ENV, "http://example.com/x.zip")
    with pytest.raises(archive.WorkBuddyFetchError):
        archive.market_url(archive.SKILL_MARKET_URL_ENV, archive.SKILL_MARKET_URL)
    monkeypatch.delenv(archive.SKILL_MARKET_URL_ENV)
    assert archive.market_url(archive.SKILL_MARKET_URL_ENV, archive.SKILL_MARKET_URL) == (
        archive.SKILL_MARKET_URL
    )
