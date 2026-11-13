"""Contract tests for the content team: scout, fact-checker, publisher and the writer's pack-writing skill."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.agents.experts.catalog import (
    WORKSPACE_MANIFEST_PATH,
    ExpertCatalog,
    default_library_root,
    discover_seed_paths,
    seed_expert_directory,
)
from octop.infra.agents.experts.skillhub_market import _SCENE_ORDER

LIBRARY = default_library_root()
REPO = Path(__file__).resolve().parents[3]
EXPERTS = {
    "content-scout": ("内容采集员", {"topic-research", "source-pack"}),
    "fact-checker": ("内容核验员", {"fact-check", "review-report"}),
    "article-publisher": ("发布专员", {"publish-gate", "wechat-draft"}),
    "article-illustrator": ("推文配图专家", {"image-sourcing", "figure-pack"}),
    "shared-folder-keeper": ("共享文件夹管理员", {"shared-cleanup"}),
}
PACK_COPIES = [
    LIBRARY / "content-scout/skills/source-pack/scripts/article_pack.py",
    LIBRARY / "fact-checker/skills/fact-check/scripts/article_pack.py",
    LIBRARY / "article-publisher/skills/publish-gate/scripts/article_pack.py",
    LIBRARY / "article-publisher/skills/wechat-draft/scripts/article_pack.py",
    LIBRARY / "wechat-writer/skills/pack-writing/scripts/article_pack.py",
    LIBRARY / "article-illustrator/skills/figure-pack/scripts/article_pack.py",
]
CHECK_DIR = LIBRARY / "fact-checker/skills/fact-check/scripts"
PACK = CHECK_DIR / "article_pack.py"
TRACE = CHECK_DIR / "claim_trace.py"
OVERLAP = CHECK_DIR / "overlap_check.py"
COMPLIANCE = CHECK_DIR / "compliance_check.py"
FORMAT = CHECK_DIR / "format_check.py"
SOURCE = LIBRARY / "content-scout/skills/source-pack/scripts/source_pack.py"
DRAFT = LIBRARY / "article-publisher/skills/wechat-draft/scripts/wechat_draft.py"
EXAMPLE = LIBRARY / "fact-checker/skills/fact-check/references/example-pack.md"
COVER = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f00050001ff"
    "89ffc3d90000000049454e44ae426082"
)


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WECHAT_")}
    env.update(
        {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}, **extra
    )
    return env


def _run(
    script: Path,
    *args: str | Path,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", env=env or _env(), input=stdin, cwd=cwd, timeout=120,
    )  # fmt: skip


def _load_pack_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("article_pack_under_test", PACK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = (
        sys.dont_write_bytecode,
        True,
    )  # keep __pycache__ out of the template dirs
    try:
        sys.modules["article_pack_under_test"] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    return mod


ap = _load_pack_module()


def _variant(
    tmp: Path, *, draft=None, sources=None, report=None, rehash: bool = True, name: str = "pack.md"
) -> Path:  # type: ignore[no-untyped-def]
    """Copy the example pack, edit parts, and re-point the review report at the (edited) draft."""
    pack = ap.parse_pack(EXAMPLE.read_text(encoding="utf-8"))
    if draft:
        pack.sections["初稿"] = draft(pack.sections["初稿"])
    if sources:
        pack.sections["线索包"] = sources(pack.sections["线索包"])
    if report:
        pack.sections["核验报告"] = report(pack.sections["核验报告"])
    if rehash and "核验报告" in pack.sections:
        pack.sections["核验报告"] = re.sub(
            r"(?m)^- 核验对象：.*$",
            f"- 核验对象：{ap.text_hash(pack.sections['初稿'])}",
            pack.sections["核验报告"],
        )
    path = tmp / name
    ap.write_pack(path, pack)
    return path


def _swap(old: str, new: str):  # type: ignore[no-untyped-def]
    def fn(text: str) -> str:
        assert old in text, old
        return text.replace(old, new)

    return fn


def _kinds(path: Path, script: Path = TRACE) -> dict[str, list[str]]:
    proc = _run(script, path, "--json")
    assert proc.stdout, proc.stderr
    out: dict[str, list[str]] = {}
    for f in json.loads(proc.stdout)["发现"]:
        out.setdefault(f["kind"], []).append(f["detail"])
    return out


# ------------------------------------------------------------------ catalog


@pytest.mark.parametrize("expert_id", sorted(EXPERTS))
def test_expert_catalog_manifest_and_skills(expert_id: str) -> None:
    label, skills = EXPERTS[expert_id]
    expert_dir = LIBRARY / expert_id
    catalog = ExpertCatalog(LIBRARY)
    catalog.refresh()
    expert = catalog.get(expert_id)
    assert expert is not None
    assert expert.summary.label_zh == label
    assert expert.summary.icon_url == f"/experts/avatars/{expert_id}.svg"
    assert expert.summary.scene in _SCENE_ORDER

    manifest = json.loads((expert_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == expert_id
    for rel in manifest["prompt_files"]:
        assert (expert_dir / rel).is_file()
    assert len(manifest["quick_prompts"]) == 6
    assert len(manifest["task_examples"]["zh"]) == len(manifest["task_examples"]["en"]) == 6

    assert {p.name for p in (expert_dir / "skills").iterdir() if p.is_dir()} == skills
    soul = (expert_dir / "SOUL.md").read_text(encoding="utf-8")
    for name in skills:
        raw = (expert_dir / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert raw.startswith(f"---\nname: {name}\n")
        assert "\ndescription:" in raw.split("---", 2)[1]
        assert f"`{name}`" in soul
        for rel in re.findall(r"`((?:scripts|references)/[\w.-]+)`", raw):
            assert (expert_dir / "skills" / name / rel).is_file(), f"{name}: {rel}"
    svg = (REPO / f"dashboard/public/experts/avatars/{expert_id}.svg").read_text(encoding="utf-8")
    assert f'aria-label="{label}"' in svg and f"bg-{expert_id}" in svg


@pytest.mark.parametrize("expert_id", sorted(EXPERTS))
async def test_expert_seeds_into_workspace(expert_id: str, tmp_path: Path) -> None:
    expert_dir = LIBRARY / expert_id
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(tmp_path), virtual_mode=False), tmp_path
    )
    paths = discover_seed_paths(expert_dir)
    assert not [p for p in paths if "__pycache__" in p]
    assert await seed_expert_directory(expert_dir=expert_dir, workspace=workspace) == len(paths) + 1
    assert (
        await workspace.adownload_bytes(WORKSPACE_MANIFEST_PATH)
        == (expert_dir / "manifest.json").read_bytes()
    )


def test_writer_gained_pack_writing_without_losing_its_skills() -> None:
    writer = LIBRARY / "wechat-writer"
    skills = {p.name for p in (writer / "skills").iterdir() if p.is_dir()}
    assert skills == {
        "article-writing",
        "headline",
        "platform-adapt",
        "topic-planning",
        "wechat-layout",
        "pack-writing",
    }
    soul = (writer / "SOUL.md").read_text(encoding="utf-8")
    assert "`pack-writing`" in soul and "article-publisher" in soul
    assert "pack-writing" in (writer / "AGENTS.md").read_text(encoding="utf-8")
    raw = (writer / "skills/pack-writing/SKILL.md").read_text(encoding="utf-8")
    assert raw.startswith("---\nname: pack-writing\n")
    manifest = json.loads((writer / "manifest.json").read_text(encoding="utf-8"))
    assert "内容团队" in manifest["description"]["zh"]


def test_shared_scripts_are_identical_copies() -> None:
    first = PACK_COPIES[0].read_bytes()
    for copy in PACK_COPIES[1:]:
        assert copy.read_bytes() == first, f"{copy} differs from {PACK_COPIES[0]}"
    layout = (LIBRARY / "wechat-writer/skills/wechat-layout/scripts/wechat_layout.py").read_bytes()
    for copy in (
        LIBRARY / "article-publisher/skills/wechat-draft/scripts/wechat_layout.py",
        LIBRARY / "article-illustrator/skills/figure-pack/scripts/wechat_layout.py",
    ):
        assert copy.read_bytes() == layout, f"{copy} differs"


def test_team_doc_and_guide_exist() -> None:
    doc = (REPO / "docs/content-team.md").read_text(encoding="utf-8")
    for needle in (
        "content-scout",
        "fact-checker",
        "article-publisher",
        "wechat-writer",
        "article-illustrator",
        "shared-folder-keeper",
        "一题一篇",
        "团队准则",
        "铁律",
    ):
        assert needle in doc


# --------------------------------------------------------------- article_pack


def test_example_pack_is_clean_and_publishable() -> None:
    assert _run(PACK, "validate", EXAMPLE).returncode == 0
    show = _run(PACK, "show", EXAMPLE, "--json")
    info = json.loads(show.stdout)
    assert info["核验结论"] == "通过" and info["核验对应当前初稿"] is True
    assert info["来源状态"] == {"可用": 2, "存疑": 1, "不可用": 1}
    assert info["引用的来源"] == ["S1", "S2", "S3"]  # S4 (rumour) is never cited
    blocked = _run(PACK, "gate", EXAMPLE, "--for", "publish")
    assert blocked.returncode == 3 and "明确确认" in blocked.stdout
    assert _run(PACK, "gate", EXAMPLE, "--for", "publish", "--confirmed").returncode == 0


def test_new_put_get_hash_roundtrip(tmp_path: Path) -> None:
    pack = tmp_path / "a" / "p.md"
    assert _run(PACK, "new", pack, "--topic", "测试话题", "--audience", "读者").returncode == 0
    assert _run(PACK, "new", pack, "--topic", "x").returncode == 1  # never overwrites
    assert _run(PACK, "new", pack, "--topic", " ", "--force").returncode == 1
    body = tmp_path / "draft.md"
    body.write_text("# 标题\n\n## 小标题\n正文 [S1]。\n", encoding="utf-8")
    put = _run(PACK, "put", pack, "初稿", "--file", body)
    assert put.returncode == 0 and "已写入“初稿”" in put.stdout
    got = _run(PACK, "get", pack, "初稿", "-o", tmp_path / "out.md")
    assert got.returncode == 0 and "## 小标题" in (tmp_path / "out.md").read_text(
        encoding="utf-8"
    )  # inner ## kept
    h1 = _run(PACK, "hash", pack).stdout.strip()
    assert re.fullmatch(r"sha256:[0-9a-f]{12}", h1)
    assert (
        _run(PACK, "put", pack, "初稿", "--file", "-", stdin="# 标题\n\n改了。\n").returncode == 0
    )
    assert _run(PACK, "hash", pack).stdout.strip() != h1
    assert _run(PACK, "get", pack, "发布单").returncode == 1  # not there yet
    assert _run(PACK, "put", pack, "随便", "--file", body).returncode == 1
    assert _run(PACK, "put", pack, "初稿", "--file", "-", stdin="  \n").returncode == 1
    plain = tmp_path / "plain.md"
    plain.write_text("# 不是文章包\n", encoding="utf-8")
    assert "不是文章包" in _run(PACK, "put", plain, "初稿", "--file", body).stderr
    assert _run(PACK, "show", tmp_path / "missing.md").returncode == 1


def test_editing_the_draft_expires_the_review(tmp_path: Path) -> None:
    pack = _variant(tmp_path, draft=_swap("补贴多少", "补贴标准"), rehash=False)
    assert _run(PACK, "validate", pack).returncode == 3
    assert "核验报告已过期" in _run(PACK, "validate", pack).stdout
    assert json.loads(_run(PACK, "show", pack, "--json").stdout)["核验对应当前初稿"] is False
    gate = _run(PACK, "gate", pack, "--for", "publish", "--confirmed")
    assert gate.returncode == 3 and "已过期" in gate.stdout


def test_put_draft_warns_when_report_goes_stale(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    changed = tmp_path / "d.md"
    changed.write_text(
        ap.parse_pack(pack.read_text(encoding="utf-8")).sections["初稿"] + "\n多了一句。\n",
        encoding="utf-8",
    )
    out = _run(PACK, "put", pack, "初稿", "--file", changed).stdout
    assert "需要重新核验" in out


def test_gate_requires_a_passing_verdict(tmp_path: Path) -> None:
    for verdict in ("需修改", "不通过"):
        pack = _variant(
            tmp_path, report=_swap("- 结论：通过", f"- 结论：{verdict}"), name=f"{verdict}.md"
        )
        gate = _run(PACK, "gate", pack, "--for", "publish", "--confirmed")
        assert gate.returncode == 3 and f"核验结论是“{verdict}”" in gate.stdout
    bad = _variant(tmp_path, report=_swap("- 结论：通过", "- 结论：差不多"), name="bad.md")
    assert "结论“差不多”" in _run(PACK, "validate", bad).stdout
    unreviewed = tmp_path / "u.md"
    pack = ap.parse_pack(EXAMPLE.read_text(encoding="utf-8"))
    del pack.sections["核验报告"]
    ap.write_pack(unreviewed, pack)
    gate = _run(PACK, "gate", unreviewed, "--for", "publish", "--confirmed")
    assert gate.returncode == 3 and "没有核验报告" in gate.stdout
    assert _run(PACK, "gate", EXAMPLE, "--for", "send").returncode == 1


def test_validate_catches_source_and_citation_problems(tmp_path: Path) -> None:
    text = lambda p: _run(PACK, "validate", p).stdout  # noqa: E731
    cites_rumour = _variant(tmp_path, draft=_swap("[S3]", "[S4]"), name="rumour.md")
    assert "引用了状态为“不可用”的来源 [S4]" in text(cites_rumour)
    ghost = _variant(tmp_path, draft=_swap("[S3]", "[S9]"), name="ghost.md")
    assert "引用了不存在的来源 [S9]" in text(ghost)
    no_excerpt = _variant(
        tmp_path,
        sources=lambda s: re.sub(r"(?s)(### S2.*?- 摘录：\n)(?:  > .*\n?)+", r"\1", s),
        name="noexcerpt.md",
    )
    assert "S2：没有摘录" in text(no_excerpt)
    bad_fields = _variant(
        tmp_path,
        sources=lambda s: s.replace("- 可信度：B", "- 可信度：Z", 1).replace("- 发布日期：2026-10-22", "- 发布日期：昨天", 1).replace("https://example.com/daily", "ftp://example.com/daily", 1),
        name="fields.md",
    )  # fmt: skip
    out = text(bad_fields)
    assert "可信度“Z”" in out and "发布日期“昨天”" in out and "网址必须以 http" in out
    dup = _variant(tmp_path, sources=lambda s: s.replace("### S2", "### S1", 1), name="dup.md")
    assert "来源编号重复" in text(dup)
    plain = tmp_path / "plain.md"
    plain.write_text("## 初稿\n\n# 标题\n", encoding="utf-8")
    assert "缺少 “article-pack: 1”" in text(plain)
    warnings_only = _run(PACK, "validate", EXAMPLE, "--json")
    assert [i["level"] for i in json.loads(warnings_only.stdout)] == ["warn"]  # S3 is 存疑


# ---------------------------------------------------------------- claim_trace


def test_claim_trace_passes_the_example_with_one_manual_item() -> None:
    proc = _run(TRACE, EXAMPLE, "--json")
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res["统计"]["必须处理"] == 0 and res["统计"]["请确认"] == 1
    only = res["发现"][0]
    assert only["kind"] == "需人工复算" and "100千瓦" in only["detail"]
    assert res["没被引用的可用来源"] == []


@pytest.mark.parametrize(
    ("old", "new", "kind", "needle"),
    [
        ("预计为2亿元 [S2]", "预计为5亿元 [S2]", "对不上", "5亿"),
        ("11月15日开始受理 [S2]", "11月15日开始受理 [S1]", "引用错位", "S2"),
        ("预计为2亿元 [S2]", "预计为2亿元", "缺来源编号", "S2"),
        ("预计为2亿元 [S2]", "预计为2亿元，同比增长35%", "无出处", "35%"),
        ("2026年11月1日开始", "2026年12月1日开始", "对不上", "2026年12月1日"),
        ("单桩补贴上限为3万元”", "单桩补贴上限为4万元”", "引语对不上", "最接近的原文"),
    ],
)
def test_claim_trace_flags_bad_claims(
    tmp_path: Path, old: str, new: str, kind: str, needle: str
) -> None:
    pack = _variant(tmp_path, draft=_swap(old, new))
    assert _run(TRACE, pack).returncode == 3
    found = _kinds(pack)
    assert kind in found, found
    assert any(needle in d for d in found[kind]), found[kind]


def test_claim_trace_unit_normalisation_and_trivial_numbers(tmp_path: Path) -> None:
    ok = _variant(
        tmp_path, draft=_swap("补贴资金总额预计为2亿元", "补贴资金总额预计为20000万元，分3个阶段")
    )
    assert (
        _run(TRACE, ok).returncode == 0
    )  # 2亿 == 20000万; "3个" is an ordinal-ish small count, not a claim
    pct = _variant(
        tmp_path, draft=_swap("预计为2亿元 [S2]", "预计为2亿元，覆盖80% [S2]"), name="pct.md"
    )
    assert "数字“80%”" in _kinds(pct)["对不上"][0]


def test_claim_trace_hedging_absolutes_and_names(tmp_path: Path) -> None:
    no_hedge = _variant(
        tmp_path,
        draft=_swap(
            "有网友反映，以往类似补贴的审批要三个月左右，但这只是个别说法，不能当作准信 [S3]",
            "以往类似补贴的审批要三个月 [S3]",
        ),
    )
    found = _kinds(no_hedge)
    assert "存疑写成事实" in found and "S3" in found["存疑写成事实"][0]
    absolute = _variant(
        tmp_path,
        draft=_swap("可以按功率拿到一次性补贴 [S1]", "是全国首个按功率补贴的政策 [S1]"),
        name="abs.md",
    )
    assert "绝对化表述" in _kinds(absolute)
    latin = _variant(
        tmp_path,
        draft=_swap(
            "可以按功率拿到一次性补贴 [S1]", "可以按功率拿到一次性补贴，参考 GB-T-2024 [S1]"
        ),
        name="latin.md",
    )
    assert "名称对不上" in _kinds(latin)
    strict = _run(TRACE, EXAMPLE, "--strict")
    assert strict.returncode == 3  # the manual-recompute item counts under --strict


def test_claim_trace_input_errors(tmp_path: Path) -> None:
    pack = ap.parse_pack(EXAMPLE.read_text(encoding="utf-8"))
    del pack.sections["线索包"]
    no_sources = tmp_path / "ns.md"
    ap.write_pack(no_sources, pack)
    assert "没有线索包" in _run(TRACE, no_sources).stderr
    assert _run(TRACE, tmp_path / "missing.md").returncode == 1
    empty = tmp_path / "e.md"
    ap.write_pack(
        empty, ap.Pack({"article-pack": "1", "topic": "x"}, {"线索包": "### S1\n- 标题：a"})
    )
    assert _run(TRACE, empty).returncode == 1


# -------------------------------------------------------------------- overlap


def test_overlap_detects_copying_and_ignores_quotes(tmp_path: Path) -> None:
    assert _run(OVERLAP, EXAMPLE).returncode == 0
    copied = _variant(
        tmp_path,
        draft=lambda d: d + "\n\n示例市发改委表示，补贴资金总额预计为2亿元，首批申请11月15日开始受理，业内人士认为补贴将带动约5000个公共充电桩落地。\n",
    )  # fmt: skip
    proc = _run(OVERLAP, copied, "--json")
    res = json.loads(proc.stdout)
    assert res["逐字重合比例"] > 0.1 and "S2" in res["分来源"] and res["最长连续片段"]["长度"] >= 40
    heavy = tmp_path / "heavy.md"
    pack = ap.parse_pack(EXAMPLE.read_text(encoding="utf-8"))
    excerpt = "\n".join(s.excerpt for s in pack.sources() if s.id in ("S1", "S2"))
    pack.sections["初稿"] = "# 标题\n\n" + excerpt.replace("\n", "\n\n") + "\n"
    ap.write_pack(heavy, pack)
    assert _run(OVERLAP, heavy).returncode == 3
    assert json.loads(_run(OVERLAP, heavy, "--json").stdout)["判断"] == "必须处理"


def test_overlap_extra_text_and_arguments(tmp_path: Path) -> None:
    full = tmp_path / "page.txt"
    full.write_text(
        "这是来源页面里一段很长的完整正文，里面有一句话叫做补贴将分三批拨付到企业账户上。",
        encoding="utf-8",
    )
    pack = _variant(
        tmp_path, draft=lambda d: d + "\n\n消息称，补贴将分三批拨付到企业账户上，请留意。\n"
    )
    plain = json.loads(_run(OVERLAP, pack, "--json").stdout)
    extra = json.loads(_run(OVERLAP, pack, "--extra", full, "--min-run", "10", "--json").stdout)
    assert "附加正文1" in extra["分来源"] and "附加正文1" not in plain["分来源"]
    assert _run(OVERLAP, pack, "--min-run", "3").returncode == 1
    assert _run(OVERLAP, pack, "--extra", tmp_path / "nope.txt").returncode == 1


# ----------------------------------------------------------------- compliance


def test_compliance_levels_and_commercial_flag(tmp_path: Path) -> None:
    assert _run(COMPLIANCE, EXAMPLE).returncode == 0
    risky = _variant(
        tmp_path,
        draft=lambda d: d.replace("# 新建充电桩", "# 震惊！新建充电桩", 1) + "\n\n这是最佳方案，包治百病，稳赚不赔。转发朋友圈领红包。联系 13812345678。\n",
    )  # fmt: skip
    proc = _run(COMPLIANCE, risky, "--json")
    hits = json.loads(proc.stdout)
    by = {(h["category"], h["word"]): h["level"] for h in hits}
    assert proc.returncode == 3
    assert by[("医疗健康功效", "包治")] == "必须处理" and by[("金融投资承诺", "稳赚")] == "必须处理"
    assert by[("个人信息", "13812345678")] == "必须处理"
    assert by[("广告法极限用语", "最佳")] == "请确认" and by[("标题党", "震惊")] == "请确认"
    commercial = {
        (h["category"], h["word"]): h["level"]
        for h in json.loads(_run(COMPLIANCE, risky, "--commercial", "--json").stdout)
    }
    assert commercial[("广告法极限用语", "最佳")] == "必须处理"
    only_title = _variant(
        tmp_path,
        draft=lambda d: d.replace("# 新建充电桩", "# 震惊！新建充电桩", 1),
        name="title.md",
    )
    assert _run(COMPLIANCE, only_title).returncode == 0  # clickbait alone is a "please confirm"


def test_compliance_promo_disclosure_plain_files_and_errors(tmp_path: Path) -> None:
    promo = tmp_path / "promo.md"
    promo.write_text("# 好物\n\n限时优惠，点击链接下单。\n", encoding="utf-8")
    assert "推广未标注" in _run(COMPLIANCE, promo).stdout
    promo.write_text("# 好物（广告）\n\n限时优惠，点击链接下单。\n", encoding="utf-8")
    assert "推广未标注" not in _run(COMPLIANCE, promo).stdout
    assert _run(COMPLIANCE, tmp_path / "missing.md").returncode == 1
    sources = _variant(tmp_path, draft=_swap("补贴多少", "补贴多少 [S1]"))
    assert (
        "[S1]" not in _run(COMPLIANCE, sources, "--json").stdout
    )  # markers are stripped before matching


# --------------------------------------------------------------------- format


def test_format_check_passes_example_and_applies_platform_limits(tmp_path: Path) -> None:
    assert _run(FORMAT, EXAMPLE).returncode == 0
    xhs = _run(FORMAT, EXAMPLE, "--platform", "xiaohongshu", "--json")
    msgs = [f["message"] for f in json.loads(xhs.stdout)]
    assert xhs.returncode == 3 and any("超过 xiaohongshu 的 20 字上限" in m for m in msgs)
    assert any("#话题" in m for m in msgs)
    relaxed = _run(
        FORMAT, EXAMPLE, "--platform", "xiaohongshu", "--title-max", "80", "--body-max", "9999"
    )
    assert (
        relaxed.returncode == 0 and "#话题" in relaxed.stdout
    )  # overridden limits pass; the missing hashtag is only a "please confirm"
    generic = _run(FORMAT, EXAMPLE, "--platform", "generic")
    assert generic.returncode == 0 and "没有发现问题" in generic.stdout


def test_format_check_catches_structure_problems(tmp_path: Path) -> None:
    broken = _variant(
        tmp_path,
        draft=lambda d: d + "\n\n#### 跳级标题\n\n![](chart.png)\n\n![图一](http://example.com/a.png)\n\n![图二](missing.png)\n\n这里还有〔待核实〕和 TODO。\n\n"
        + "很长的段落" * 80 + "\n",
    )  # fmt: skip
    res = json.loads(_run(FORMAT, broken, "--json").stdout)
    text = "\n".join(f"{f['level']} {f['where']} {f['message']}" for f in res)
    for needle in (
        "层级不连续",
        "没有说明文字",
        "是 http 地址",
        "本地图片 missing.png 找不到",
        "占位符“〔待核实〕”",
        "占位符“TODO”",
        "手机上读起来太长",
    ):
        assert needle in text, needle
    (tmp_path / "chart.png").write_bytes(COVER)
    fixed = json.loads(_run(FORMAT, broken, "--json").stdout)
    assert not any(
        "本地图片 chart.png 找不到" in f["message"] for f in fixed
    )  # a local image next to the pack is found
    short = _variant(tmp_path, draft=lambda d: "# 好\n\n太短。\n", name="short.md")
    msgs = [f["message"] for f in json.loads(_run(FORMAT, short, "--json").stdout)]
    assert (
        any("标题只有 1 字" in m for m in msgs)
        and any("至少 300 字" in m for m in msgs)
        and any("没有“摘要：”行" in m for m in msgs)
    )
    assert _run(FORMAT, tmp_path / "missing.md").returncode == 1


# ----------------------------------------------------------------- source_pack


class _Web(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence
        pass

    def do_GET(self) -> None:
        page = {
            "/news.html": (
                200,
                "text/html; charset=utf-8",
                (
                    '<html><head><meta charset="utf-8"><title>示例日报：充电桩补贴</title>'
                    '<meta property="og:site_name" content="示例日报">'
                    '<meta property="article:published_time" content="2026-10-22T08:00:00+08:00"></head>'
                    "<body><nav>首页 导航文字</nav><article><h1>充电桩补贴</h1>"
                    "<p>示例市发改委表示，补贴资金总额预计为2亿元，首批申请11月15日开始受理。</p>"
                    "<p>业内人士认为，补贴将带动约5000个公共充电桩落地。</p><p>短句。</p></article>"
                    "<footer>版权所有 补贴</footer><script>var x='补贴脚本';</script></body></html>"
                ).encode(),
            ),
            "/ld.html": (
                200,
                "text/html",
                (
                    '<html><head><title>T</title><script type="application/ld+json">{"datePublished":"2026-09-30T00:00:00Z"}</script>'
                    "</head><body><p>这是一句足够长的正文内容。</p></body></html>"
                ).encode(),
            ),
            "/file.pdf": (200, "application/pdf", b"%PDF"),
        }.get(self.path.split("?")[0], (404, "text/plain", b"missing"))
        self.send_response(page[0])
        self.send_header("Content-Type", page[1])
        self.end_headers()
        self.wfile.write(page[2])


@pytest.fixture
def web() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Web)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_extracts_verbatim_sentences_and_metadata(web: str) -> None:
    proc = _run(SOURCE, "fetch", f"{web}/news.html", "--match", "补贴", "--json")
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout)
    assert info["发布日期"] == "2026-10-22" and info["发布方"] == "示例日报"
    assert (
        "示例市发改委表示，补贴资金总额预计为2亿元，首批申请11月15日开始受理。" in info["摘录候选"]
    )  # the whole original sentence
    joined = "".join(info["摘录候选"])
    assert (
        "导航文字" not in joined and "补贴脚本" not in joined and "版权所有" not in joined
    )  # nav/script/footer dropped
    assert "短句。" not in info["摘录候选"]
    assert (
        json.loads(_run(SOURCE, "fetch", f"{web}/ld.html", "--json").stdout)["发布日期"]
        == "2026-09-30"
    )  # JSON-LD
    assert _run(SOURCE, "fetch", f"{web}/nope.html").returncode == 1
    assert "不是网页" in _run(SOURCE, "fetch", f"{web}/file.pdf").stderr
    assert "http" in _run(SOURCE, "fetch", "ftp://x/y").stderr


def test_pack_building_flow(tmp_path: Path, web: str) -> None:
    pack = tmp_path / "p.md"
    assert _run(PACK, "new", pack, "--topic", "充电桩").returncode == 0
    added = _run(
        SOURCE,
        "fetch",
        f"{web}/news.html",
        "--match",
        "补贴",
        "--add-to",
        pack,
        "--credibility",
        "B",
    )
    assert added.returncode == 0 and "S1" in added.stdout
    assert (
        _run(
            SOURCE,
            "fetch",
            f"{web}/news.html",
            "--match",
            "不存在的词",
            "--add-to",
            pack,
            "--credibility",
            "B",
        ).returncode
        == 1
    )
    assert (
        _run(SOURCE, "fetch", f"{web}/news.html", "--match", "补贴", "--add-to", pack).returncode
        == 1
    )  # needs --credibility
    manual = _run(
        SOURCE,
        "add",
        pack,
        "--title",
        "官方通知",
        "--url",
        f"{web}/gov.html",
        "--credibility",
        "A",
        "--date",
        "2026-10-20",
        "--excerpt",
        "自2026年11月1日起补贴。",
    )
    assert manual.returncode == 0 and "S2" in manual.stdout
    assert (
        _run(
            SOURCE, "add", pack, "--title", "无摘录", "--url", f"{web}/x", "--credibility", "A"
        ).returncode
        == 1
    )
    assert (
        _run(
            SOURCE,
            "add",
            pack,
            "--title",
            "x",
            "--url",
            f"{web}/x",
            "--credibility",
            "Z",
            "--excerpt",
            "a",
        ).returncode
        == 1
    )
    assert (
        _run(
            SOURCE,
            "add",
            pack,
            "--title",
            "x",
            "--url",
            f"{web}/x",
            "--credibility",
            "A",
            "--date",
            "昨天",
            "--excerpt",
            "a",
        ).returncode
        == 1
    )
    rumour = _run(
        SOURCE,
        "add",
        pack,
        "--title",
        "传言",
        "--url",
        f"{web}/rumour",
        "--credibility",
        "D",
        "--status",
        "不可用",
        "--note",
        "无出处",
    )
    assert rumour.returncode == 0 and "S3" in rumour.stdout  # an unusable source needs no excerpt
    assert _run(PACK, "validate", pack).returncode == 0
    listing = _run(SOURCE, "list", pack).stdout
    assert "S1  [B/可用]" in listing and "S3  [D/不可用]" in listing
    timeline = _run(SOURCE, "timeline", pack).stdout.splitlines()
    assert (
        timeline[0].startswith("2026-10-20")
        and timeline[1].startswith("2026-10-22")
        and timeline[2].startswith("未知")
    )

    check = _run(SOURCE, "check", pack, "--write")
    assert (
        check.returncode == 3
        and "✓ S1 200" in check.stdout
        and "✗ S2 404" in check.stdout
        and "S3" not in check.stdout
    )
    text = pack.read_text(encoding="utf-8")
    assert (
        re.search(r"(?s)### S1.*?- 状态：可用\n- 链接检查：200 @", text)
        and "- 链接检查：404 @" in text
    )
    assert _run(PACK, "validate", pack).returncode == 0  # the extra field does not break the format


def test_dedupe_marks_reposts_by_url_and_excerpt(tmp_path: Path, web: str) -> None:
    pack = tmp_path / "p.md"
    _run(PACK, "new", pack, "--topic", "x")
    base = ["--title", "t", "--credibility", "B"]
    _run(
        SOURCE,
        "add",
        pack,
        *base,
        "--url",
        f"{web}/a?utm_source=wx",
        "--excerpt",
        "示例市发改委表示补贴资金总额预计为2亿元首批申请11月15日开始受理",
    )
    _run(SOURCE, "add", pack, *base, "--url", f"{web}/a/#top", "--excerpt", "完全不同的另一句话")
    _run(
        SOURCE,
        "add",
        pack,
        *base,
        "--url",
        f"{web}/b",
        "--excerpt",
        "示例市发改委表示，补贴资金总额预计为2亿元，首批申请11月15日开始受理！",
    )
    _run(
        SOURCE,
        "add",
        pack,
        *base,
        "--url",
        f"{web}/c",
        "--excerpt",
        "毫不相关的内容讲的是另外一件事情",
    )
    proc = _run(SOURCE, "dedupe", pack, "--mark")
    assert proc.returncode == 3
    assert (
        "S2：网址与 S1 相同" in proc.stdout
        and "S3：摘录与 S1 高度相似" in proc.stdout
        and "S4" not in proc.stdout
    )
    text = pack.read_text(encoding="utf-8")
    assert text.count("重复：") == 2
    clean = tmp_path / "c.md"
    _run(PACK, "new", clean, "--topic", "x")
    _run(SOURCE, "add", clean, *base, "--url", f"{web}/a", "--excerpt", "第一句话说的是甲")
    _run(SOURCE, "add", clean, *base, "--url", f"{web}/b", "--excerpt", "第二句话说的是乙乙乙乙")
    assert _run(SOURCE, "dedupe", clean).returncode == 0


# ---------------------------------------------------------------- wechat_draft


class _Wechat(BaseHTTPRequestHandler):
    log: list[tuple[str, str]] = []
    articles: list[dict[str, object]] = []
    fail: dict[str, int] = {}
    expire_once = False

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.split("?")[0].rsplit("/cgi-bin/", 1)[-1]
        _Wechat.log.append((path, self.path))
        out: dict[str, object]
        if path in _Wechat.fail:
            out = {"errcode": _Wechat.fail[path], "errmsg": "forced"}
        elif path != "stable_token" and _Wechat.expire_once:
            _Wechat.expire_once = False
            out = {"errcode": 42001, "errmsg": "access_token expired"}
        elif path == "stable_token":
            out = {
                "access_token": f"TOKEN{sum(1 for p, _ in _Wechat.log if p == 'stable_token')}",
                "expires_in": 7200,
            }
        elif path == "material/add_material":
            out = {"media_id": "COVER_MEDIA"}
        elif path == "media/uploadimg":
            out = {"url": f"https://mmbiz.qpic.cn/fake/{len(body)}.png"}
        elif path == "draft/add":
            _Wechat.articles.append(json.loads(body.decode("utf-8"))["articles"][0])
            out = {"media_id": f"DRAFT{len(_Wechat.articles)}"}
        else:
            out = {"errcode": 404, "errmsg": "unknown"}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


@pytest.fixture
def wechat() -> dict[str, str]:
    _Wechat.log, _Wechat.articles, _Wechat.fail, _Wechat.expire_once = [], [], {}, False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Wechat)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {
        "WECHAT_API_BASE": f"http://127.0.0.1:{server.server_address[1]}",
        "WECHAT_APP_ID": "wx1234567890",
        "WECHAT_APP_SECRET": "top-secret-value",
    }
    server.shutdown()
    server.server_close()


def _publish_dir(tmp: Path, **variant) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    pack = _variant(tmp, **variant)
    cover = tmp / "cover.png"
    cover.write_bytes(COVER)
    return pack, cover


def _draft(
    pack: Path, cover: Path, env: dict[str, str], *extra: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return _run(DRAFT, "draft", pack, "--cover", cover, *extra, env=_env(**env), cwd=cwd)


def test_wechat_check_never_prints_the_secret(wechat: dict[str, str]) -> None:
    ok = _run(DRAFT, "check", "--online", env=_env(**wechat))
    assert (
        ok.returncode == 0
        and "wx12****" in ok.stdout
        and "top-secret-value" not in ok.stdout
        and "access_token 获取成功" in ok.stdout
    )
    missing = _run(DRAFT, "check")
    assert missing.returncode == 1 and "未设置" in missing.stdout
    _Wechat.fail["stable_token"] = 40164
    blocked = _run(DRAFT, "check", "--online", env=_env(**wechat))
    assert blocked.returncode == 3 and "IP 白名单" in blocked.stdout


def test_draft_dry_run_touches_nothing(tmp_path: Path, wechat: dict[str, str]) -> None:
    pack, cover = _publish_dir(tmp_path)
    before = pack.read_text(encoding="utf-8")
    proc = _draft(pack, cover, wechat, "--confirmed", "--dry-run", cwd=tmp_path)
    assert proc.returncode == 0 and "试运行" in proc.stdout and "参考来源 3 条" in proc.stdout
    assert (
        _Wechat.log == []
        and pack.read_text(encoding="utf-8") == before
        and not (tmp_path / "outputs").exists()
    )


def test_draft_is_refused_until_every_gate_passes(tmp_path: Path, wechat: dict[str, str]) -> None:
    pack, cover = _publish_dir(tmp_path)
    unconfirmed = _draft(pack, cover, wechat, cwd=tmp_path)
    assert unconfirmed.returncode == 3 and "明确确认" in unconfirmed.stdout
    stale, cover2 = _publish_dir(
        tmp_path, draft=_swap("补贴多少", "补贴标准"), rehash=False, name="stale.md"
    )
    assert "已过期" in _draft(stale, cover2, wechat, "--confirmed", cwd=tmp_path).stdout
    failed, _ = _publish_dir(
        tmp_path, report=_swap("- 结论：通过", "- 结论：需修改"), name="failed.md"
    )
    assert "只有“通过”才能发布" in _draft(failed, cover, wechat, "--confirmed", cwd=tmp_path).stdout
    assert _Wechat.log == []  # nothing reached the API
    assert (
        _draft(pack, tmp_path / "no-cover.png", wechat, "--confirmed", cwd=tmp_path).returncode == 1
    )


def test_draft_uploads_and_records_everything(tmp_path: Path, wechat: dict[str, str]) -> None:
    (tmp_path / "chart.png").write_bytes(COVER)
    pack, cover = _publish_dir(
        tmp_path, draft=lambda d: d.replace("## 要等多久", "![走势图](chart.png)\n\n## 要等多久", 1)
    )
    proc = _draft(pack, cover, wechat, "--confirmed", "--author", "小编", "--comment", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DRAFT1" in proc.stdout and "手动发布" in proc.stdout
    assert [p for p, _ in _Wechat.log] == [
        "stable_token",
        "material/add_material",
        "media/uploadimg",
        "draft/add",
    ]  # one token for the whole run
    assert "type=image" in _Wechat.log[1][1] and "access_token=TOKEN1" in _Wechat.log[3][1]
    article = _Wechat.articles[0]
    assert (
        article["author"] == "小编"
        and article["thumb_media_id"] == "COVER_MEDIA"
        and article["need_open_comment"] == 1
    )
    content = str(article["content"])
    assert "[S1]" not in content and "[推算]" not in content  # review markers stripped
    assert "参考来源" in content and "示例市发展改革委" in content and "example.gov.cn" in content
    assert (
        "https://mmbiz.qpic.cn/fake/" in content and "chart.png" not in content
    )  # local image replaced by the uploaded url
    assert str(article["title"]).startswith("新建充电桩") and article["digest"].startswith(
        "示例市出台"
    )  # type: ignore[union-attr]
    saved = ap.parse_pack(pack.read_text(encoding="utf-8"))
    ticket = ap.parse_fields(saved.sections["发布单"].splitlines())
    assert (
        ticket["状态"] == "已存草稿"
        and ticket["media_id"] == "DRAFT1"
        and ticket["对应初稿"] == ap.text_hash(saved.draft())
    )
    assert all(i.level != "error" for i in ap.validate(saved))
    rows = list(
        csv.DictReader((tmp_path / "outputs/publish-ledger.csv").open(encoding="utf-8-sig"))
    )
    assert len(rows) == 1 and rows[0]["状态"] == "已存草稿" and rows[0]["media_id"] == "DRAFT1"
    assert wechat["WECHAT_APP_SECRET"] not in proc.stdout + pack.read_text(encoding="utf-8") + (
        tmp_path / "outputs/publish-ledger.csv"
    ).read_text(encoding="utf-8")


def test_same_draft_is_not_submitted_twice(tmp_path: Path, wechat: dict[str, str]) -> None:
    pack, cover = _publish_dir(tmp_path)
    assert _draft(pack, cover, wechat, "--confirmed", cwd=tmp_path).returncode == 0
    again = _draft(pack, cover, wechat, "--confirmed", cwd=tmp_path)
    assert again.returncode == 3 and "不重复提交" in again.stdout and len(_Wechat.articles) == 1
    forced = _draft(pack, cover, wechat, "--confirmed", "--force", cwd=tmp_path)
    assert forced.returncode == 0 and len(_Wechat.articles) == 2
    ledger = ["--ledger", str(tmp_path / "outputs/publish-ledger.csv")]
    h = ap.text_hash(ap.parse_pack(pack.read_text(encoding="utf-8")).draft())
    assert _run(DRAFT, "ledger", "check", "--hash", h, *ledger).returncode == 3
    assert _run(DRAFT, "ledger", "check", "--hash", "sha256:000000000000", *ledger).returncode == 0
    assert "已存草稿" in _run(DRAFT, "ledger", "list", *ledger).stdout
    added = _run(
        DRAFT,
        "ledger",
        "add",
        "--platform",
        "小红书",
        "--state",
        "已发布",
        "--title",
        "手动发的",
        *ledger,
    )
    assert added.returncode == 0 and "小红书" in _run(DRAFT, "ledger", "list", *ledger).stdout
    assert _run(DRAFT, "ledger", "add", *ledger).returncode == 1


def test_api_errors_explain_what_to_do_and_leave_a_failed_row(
    tmp_path: Path, wechat: dict[str, str]
) -> None:
    pack, cover = _publish_dir(tmp_path)
    _Wechat.fail["draft/add"] = 48001
    denied = _draft(pack, cover, wechat, "--confirmed", cwd=tmp_path)
    assert (
        denied.returncode == 3
        and "没有该接口的权限" in denied.stdout
        and "手动粘贴" in denied.stdout
    )
    assert (
        "发布单" not in ap.parse_pack(pack.read_text(encoding="utf-8")).sections
    )  # never claims success
    rows = list(
        csv.DictReader((tmp_path / "outputs/publish-ledger.csv").open(encoding="utf-8-sig"))
    )
    assert rows[0]["状态"] == "失败" and "48001" in rows[0]["说明"]
    _Wechat.fail.clear()
    assert (
        _draft(pack, cover, wechat, "--confirmed", cwd=tmp_path).returncode == 0
    )  # a failed row does not block a retry
    _Wechat.fail["material/add_material"] = 40164
    other, _ = _publish_dir(tmp_path, draft=_swap("补贴多少", "补贴额度"), name="other.md")
    assert "IP" in _draft(other, cover, wechat, "--confirmed", cwd=tmp_path).stdout


def test_expired_token_is_refreshed_once(tmp_path: Path, wechat: dict[str, str]) -> None:
    pack, cover = _publish_dir(tmp_path)
    _Wechat.expire_once = True
    assert _draft(pack, cover, wechat, "--confirmed", cwd=tmp_path).returncode == 0
    assert [p for p, _ in _Wechat.log] == [
        "stable_token",
        "material/add_material",
        "stable_token",
        "material/add_material",
        "draft/add",
    ]
    assert (
        "access_token=TOKEN2" in _Wechat.log[-1][1]
    )  # the new token is kept for the following calls


def test_render_and_export(tmp_path: Path) -> None:
    out = tmp_path / "o" / "a.html"
    proc = _run(DRAFT, "render", EXAMPLE, "-o", out, "--theme", "blue", "--author", "小编")
    assert proc.returncode == 0 and "参考来源 3 条" in proc.stdout
    html = out.read_text(encoding="utf-8")
    assert "[S1]" not in html and "[推算]" not in html and "<section" in html
    assert (
        html.count("参考来源") == 1 and "示例车主论坛" in html and "匿名自媒体" not in html
    )  # S3 is cited, the rumour S4 is not
    xhs = tmp_path / "x.txt"
    assert _run(DRAFT, "export", EXAMPLE, "--platform", "xiaohongshu", "-o", xhs).returncode == 0
    text = xhs.read_text(encoding="utf-8")
    assert text.startswith("新建充电桩") and "[S1]" not in text and "##" not in text
    assert (
        "超过 xiaohongshu"
        in _run(DRAFT, "export", EXAMPLE, "--platform", "xiaohongshu", "-o", xhs).stdout
    )
    stale = _variant(tmp_path, draft=_swap("补贴多少", "补贴标准"), rehash=False, name="stale.md")
    assert (
        _run(DRAFT, "export", stale, "--platform", "plain", "-o", tmp_path / "p.txt").returncode
        == 3
    )
    assert _run(DRAFT, "render", tmp_path / "missing.md", "-o", out).returncode == 1
