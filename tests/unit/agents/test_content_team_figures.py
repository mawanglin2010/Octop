"""Content team v2: one pack per topic, the illustrator's figure list and preview, figures in drafts,
and the shared-folder keeper's report script."""

from __future__ import annotations

import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image
from tests.unit.agents.test_content_team_experts import (  # noqa: F401 - wechat is a fixture
    DRAFT,
    EXAMPLE,
    LIBRARY,
    _env,
    _run,
    _variant,
    _Wechat,
    ap,
    wechat,
)

FIG_DIR = LIBRARY / "article-illustrator/skills/figure-pack/scripts"
FIGURES = FIG_DIR / "figures.py"
PREVIEW = FIG_DIR / "preview_pdf.py"
PACK = FIG_DIR / "article_pack.py"
REPORT = LIBRARY / "shared-folder-keeper/skills/shared-cleanup/scripts/shared_report.py"


def _jpeg(
    size: tuple[int, int] = (800, 600), color: tuple[int, int, int] = (40, 120, 200)
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG", quality=90)
    return out.getvalue()


def _fig(pack: Path, *args: str | Path, cwd: Path) -> tuple[int, str]:
    proc = _run(FIGURES, *args[:1], pack, *args[1:], cwd=cwd)
    return proc.returncode, proc.stdout + proc.stderr


COVER_WHY = ["--role", "点题", "--point", "新建充电桩按功率领补贴", "--elements", "公共充电桩",
             "--basis", "「新建充电桩每千瓦补贴300元」", "--seen", "城市街头一排崭新的公共充电桩"]  # fmt: skip
INLINE_WHY = ["--role", "说明", "--point", "直流快充桩按千瓦补贴", "--elements", "直流快充桩",
              "--basis", "「直流快充桩每千瓦补贴300元」", "--seen", "停车位旁的一台直流快充桩"]  # fmt: skip
P1_WHY = ["--role", "说明", "--point", "新桩可以领补贴", "--elements", "公共充电桩",
          "--basis", "「新建的公共充电桩」", "--seen", "路边新装的公共充电桩"]  # fmt: skip


def _illustrated(tmp: Path) -> Path:
    """Example pack (review passed) plus a generated cover and a searched inline figure."""
    pack = _variant(tmp)
    (tmp / "gen").mkdir()
    (tmp / "gen/cover.jpg").write_bytes(_jpeg((1024, 1024)))
    (tmp / "gen/body.jpg").write_bytes(_jpeg(color=(200, 120, 30)))
    rc, out = _fig(pack, "add", "--file", "gen/cover.jpg", "--mode", "生成", "--pos", "封面",
                   "--caption", "城市街头的公共充电桩", "--prompt", "充电桩，扁平插画", "--model", "seedream",
                   "--crop", "2.35:1", *COVER_WHY, cwd=tmp)  # fmt: skip
    assert rc == 0, out
    rc, out = _fig(pack, "add", "--file", "gen/body.jpg", "--mode", "搜索", "--pos", "「补贴多少」后",
                   "--caption", "直流快充桩", "--url", "https://example.gov.cn/a.jpg",
                   "--page", "https://example.gov.cn/a", "--credit", "示例市发改委",
                   "--license", "官方发布，允许转载", *INLINE_WHY, cwd=tmp)  # fmt: skip
    assert rc == 0, out
    return pack


# ---------------------------------------------------------------- one pack per topic


def test_batch_builds_one_pack_per_topic(tmp_path: Path) -> None:
    proc = _run(PACK, "batch", tmp_path / "packs", "--topic", "热点一", "--topic", "热点 二：续",
                "--topic", "热点一", "--audience", "商户")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    files = sorted(p.name for p in (tmp_path / "packs").iterdir())
    assert files == ["热点-二-续.md", "热点一.md"]  # duplicates collapsed, names sanitised
    one = ap.parse_pack((tmp_path / "packs/热点一.md").read_text(encoding="utf-8"))
    assert one.topic == "热点一" and "- 读者：商户" in one.sections["选题单"]
    again = _run(PACK, "batch", tmp_path / "packs", "--topic", "热点一")
    assert again.returncode == 1 and "已存在" in again.stderr


def test_slugify_keeps_chinese_and_drops_path_characters() -> None:
    assert ap.slugify("新能源/充电桩 补贴：2026年？") == "新能源-充电桩-补贴-2026年"
    assert ap.slugify("../..") == "topic" and len(ap.slugify("长" * 80)) == 40


# ---------------------------------------------------------------- figure list


def test_figures_never_touch_the_draft_and_validate(tmp_path: Path) -> None:
    pack = _illustrated(tmp_path)
    parsed = ap.parse_pack(pack.read_text(encoding="utf-8"))
    original = ap.parse_pack(EXAMPLE.read_text(encoding="utf-8"))
    assert parsed.draft() == original.draft() and ap.report_current(parsed) is True
    assert [i for i in ap.validate(parsed) if i.level == "error"] == []
    cover, inline = parsed.figures()
    assert (cover.id, cover.position, inline.id, inline.position) == (
        "P1",
        "封面",
        "P2",
        "「补贴多少」后",
    )
    slug = ap.slugify(parsed.topic)
    assert (
        cover.fields["文件"] == f"images/{slug}/P1.jpg"
        and cover.fields["共享名"] == f"{slug}/P1.jpg"
    )
    assert cover.caption().endswith("（AI 生成）") and inline.caption() == "直流快充桩"
    with Image.open(tmp_path / cover.fields["文件"]) as im:  # cropped to 2.35:1
        assert abs(im.size[0] / im.size[1] - 2.35) < 0.01
    assert cover.fields["哈希"] == ap.file_hash((tmp_path / cover.fields["文件"]).read_bytes())
    rc, out = _fig(pack, "check", cwd=tmp_path)
    assert rc == 0 and "配图 2 张，没有问题" in out
    rc, out = _fig(pack, "share", "--json", cwd=tmp_path)
    assert [r["共享名"] for r in json.loads(out)] == [f"{slug}/P1.jpg", f"{slug}/P2.jpg"]


def test_figure_rules(tmp_path: Path) -> None:
    pack = _illustrated(tmp_path)
    (tmp_path / "x.jpg").write_bytes(_jpeg())
    base = [
        "--file",
        "x.jpg",
        "--mode",
        "生成",
        "--prompt",
        "p",
        "--model",
        "m",
        "--caption",
        "c",
        *P1_WHY,
    ]
    assert "已经有封面" in _fig(pack, "add", *base, "--pos", "封面", cwd=tmp_path)[1]
    assert "超出范围" in _fig(pack, "add", *base, "--pos", "第99段后", cwd=tmp_path)[1]
    assert "没有这个小标题" in _fig(pack, "add", *base, "--pos", "「不存在」后", cwd=tmp_path)[1]
    assert "应为" in _fig(pack, "add", *base, "--pos", "中间", cwd=tmp_path)[1]
    (tmp_path / "fake.jpg").write_bytes(b"not an image" * 200)
    bad = [
        "--file",
        "fake.jpg",
        "--mode",
        "生成",
        "--prompt",
        "p",
        "--model",
        "m",
        "--caption",
        "c",
        *P1_WHY,
    ]
    assert "不是 JPG" in _fig(pack, "add", *bad, "--pos", "开头", cwd=tmp_path)[1]
    search = ["--file", "x.jpg", "--mode", "搜索", "--caption", "c", "--pos", "开头", *P1_WHY]
    assert "--url" in _fig(pack, "add", *search, cwd=tmp_path)[1]
    # an unknown licence is an error in the pack itself
    text = pack.read_text(encoding="utf-8").replace("- 授权：官方发布，允许转载", "- 授权：未知")
    pack.write_text(text, encoding="utf-8")
    rc, out = _fig(pack, "check", cwd=tmp_path)
    assert rc == 3 and "授权不明" in out
    # a swapped file no longer matches its hash
    pack.write_text(text.replace("- 授权：未知", "- 授权：CC BY 4.0"), encoding="utf-8")
    fig = ap.parse_pack(pack.read_text(encoding="utf-8")).figures()[1]
    (tmp_path / fig.fields["文件"]).write_bytes(_jpeg(color=(0, 0, 0)))
    rc, out = _fig(pack, "check", cwd=tmp_path)
    assert rc == 3 and "哈希不符" in out
    assert _fig(pack, "remove", "P2", cwd=tmp_path)[0] == 0
    assert [f.id for f in ap.parse_pack(pack.read_text(encoding="utf-8")).figures()] == ["P1"]
    assert _fig(pack, "remove", "P9", cwd=tmp_path)[0] == 1


def test_illustrated_lines_inserts_at_positions() -> None:
    draft = "# 标题\n\n摘要：x\n\n第一段[S1]。\n\n## 小节\n\n第二段。\n\n## 参考来源\n\n- a"
    figs = ap.parse_figures(
        "### P1\n- 位置：开头\n- 方式：生成\n- 图注：一\n\n"
        "### P2\n- 位置：「小节」后\n- 方式：搜索\n- 图注：二\n\n"
        "### P3\n- 位置：第2段后\n- 方式：生成\n- 图注：三"
    )
    out = ap.illustrated_lines(draft, figs, lambda f: f"{f.id}.jpg")
    assert out.index("![一（AI 生成）](P1.jpg)") == 1
    assert out[out.index("## 小节") + 1] == "![二](P2.jpg)"
    assert out[out.index("第二段。") + 1] == "![三（AI 生成）](P3.jpg)"
    assert ap.illustrated_lines(draft, figs, lambda f: None) == draft.splitlines()


class _Img(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        body, ctype = (
            (_jpeg(), "image/jpeg")
            if self.path == "/a.jpg"
            else (b"<html>" + b"x" * 4096 + b"</html>", "text/html")
        )
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


def test_fetch_downloads_and_requires_a_licence(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Img)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    web = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        pack = _variant(tmp_path)
        common = [
            "--page",
            f"{web}/page",
            "--credit",
            "作者甲",
            "--pos",
            "第1段后",
            "--caption",
            "现场",
            *P1_WHY,
        ]
        rc, out = _fig(
            pack, "fetch", "--url", f"{web}/a.jpg", "--license", "CC BY 4.0", *common, cwd=tmp_path
        )
        assert rc == 0, out
        fig = ap.parse_pack(pack.read_text(encoding="utf-8")).figures()[0]
        assert fig.mode == "搜索" and fig.fields["网址"] == f"{web}/a.jpg"
        assert ap.figure_credits([fig]) == [f"P1：作者甲，CC BY 4.0，{web}/page"]
        assert "授权不明" in _fig(pack, "fetch", "--url", f"{web}/a.jpg", "--license", "未知", *common,
                                  cwd=tmp_path)[1]  # fmt: skip
        assert "不是 JPG" in _fig(pack, "fetch", "--url", f"{web}/page.html", "--license", "CC0", *common,
                                  cwd=tmp_path)[1]  # fmt: skip
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- preview


def test_preview_html_has_cover_figures_and_appendix(tmp_path: Path) -> None:
    pack = _illustrated(tmp_path)
    proc = _run(PREVIEW, pack, "--html-only", "--author", "小编", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    slug = ap.slugify(ap.parse_pack(pack.read_text(encoding="utf-8")).topic)
    page = unquote((tmp_path / f"outputs/preview/{slug}.html").read_text(encoding="utf-8"))
    assert "预览稿，未发布" in page and "核验结论：通过" in page
    assert "[S1]" not in page and "城市街头的公共充电桩（AI 生成）" in page
    assert f"images/{slug}/P1.jpg" in page and f"images/{slug}/P2.jpg" in page
    assert (
        "参考来源" in page
        and "图片来源" in page
        and "官方发布，允许转载" in page
        and "配图清单" in page
    )
    assert page.index("P1.jpg") < page.index("补贴多少") < page.index("P2.jpg")


def test_preview_pdf_when_chromium_is_available(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    pack = _illustrated(tmp_path)
    out = tmp_path / "preview.pdf"
    proc = _run(PREVIEW, pack, "-o", out, cwd=tmp_path)
    if proc.returncode == 2:
        assert (tmp_path / "preview.html").is_file() and "HTML" in proc.stdout
        pytest.skip(f"Chromium is not available here: {proc.stdout.strip()}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.read_bytes().startswith(b"%PDF") and out.stat().st_size > 10_000


# ---------------------------------------------------------------- publishing with figures


def _shared_copy(pack: Path, tmp: Path) -> None:
    """Simulate shared_file_get: copy each figure to shared/<共享名> in the publisher's workspace."""
    for fig in ap.parse_pack(pack.read_text(encoding="utf-8")).figures():
        dest = tmp / "shared" / fig.fields["共享名"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((tmp / fig.fields["文件"]).read_bytes())


def test_draft_uses_the_figure_list(tmp_path: Path, wechat: dict[str, str]) -> None:  # noqa: F811
    pack = _illustrated(tmp_path)
    env = _env(**wechat)
    missing = _run(DRAFT, "figures", pack, env=env, cwd=tmp_path)
    assert missing.returncode == 3 and "shared_file_get" in missing.stdout
    refused = _run(DRAFT, "draft", pack, "--confirmed", env=env, cwd=tmp_path)
    assert refused.returncode == 3 and "配图没准备好" in refused.stdout and _Wechat.log == []

    _shared_copy(pack, tmp_path)
    assert "哈希和配图单一致" in _run(DRAFT, "figures", pack, env=env, cwd=tmp_path).stdout
    proc = _run(DRAFT, "draft", pack, "--confirmed", env=env, cwd=tmp_path)  # no --cover
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [p for p, _ in _Wechat.log] == [
        "stable_token",
        "material/add_material",
        "media/uploadimg",
        "draft/add",
    ]
    content = str(_Wechat.articles[0]["content"])
    assert (
        "https://mmbiz.qpic.cn/fake/" in content
        and "图片来源" in content
        and "示例市发改委" in content
    )
    assert content.index("补贴多少") < content.index("mmbiz.qpic.cn")


def test_draft_refuses_a_swapped_figure(tmp_path: Path, wechat: dict[str, str]) -> None:  # noqa: F811
    pack = _illustrated(tmp_path)
    _shared_copy(pack, tmp_path)
    fig = ap.parse_pack(pack.read_text(encoding="utf-8")).figures()[1]
    (tmp_path / "shared" / fig.fields["共享名"]).write_bytes(_jpeg(color=(1, 2, 3)))
    proc = _run(DRAFT, "draft", pack, "--confirmed", env=_env(**wechat), cwd=tmp_path)
    assert proc.returncode == 3 and "哈希不一致" in proc.stdout and _Wechat.log == []


def test_draft_without_figures_still_needs_a_cover(tmp_path: Path, wechat: dict[str, str]) -> None:  # noqa: F811
    pack = _variant(tmp_path)
    proc = _run(DRAFT, "draft", pack, "--confirmed", env=_env(**wechat), cwd=tmp_path)
    assert proc.returncode == 1 and "没有封面" in proc.stderr


# ---------------------------------------------------------------- shared folder keeper


def test_shared_report_groups_and_picks_old_files(tmp_path: Path) -> None:
    listing = tmp_path / "list.json"
    listing.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "name": "热点一/文章包.md",
                        "size": 2048,
                        "modified": "2026-09-01T00:00:00+00:00",
                    },
                    {
                        "name": "热点一/P1.jpg",
                        "size": 300_000,
                        "modified": "2026-09-02T00:00:00+00:00",
                    },
                    {
                        "name": "热点二/P1.jpg",
                        "size": 100_000,
                        "modified": "2026-11-10T00:00:00+00:00",
                    },
                    {"name": "notes.txt", "size": 10, "modified": "2026-08-01T00:00:00+00:00"},
                ],
                "truncated": False,
            }
        ),
        encoding="utf-8",
    )
    now = ["--now", "2026-11-14T00:00:00+00:00"]
    summary = _run(REPORT, listing, *now)
    assert summary.returncode == 0 and "全部：4 个文件" in summary.stdout
    assert re.search(r"- 热点一：2 个", summary.stdout) and "（根目录）" in summary.stdout
    old = json.loads(_run(REPORT, listing, "--older-than", "30", "--json", *now).stdout)
    assert sorted(f["name"] for f in old["files"]) == [
        "notes.txt",
        "热点一/P1.jpg",
        "热点一/文章包.md",
    ]
    jpg = json.loads(
        _run(REPORT, listing, "--pattern", "*.jpg", "--prefix", "热点二/", "--json", *now).stdout
    )
    assert [f["name"] for f in jpg["files"]] == ["热点二/P1.jpg"]
    listing.write_text(json.dumps({"error": "no user"}), encoding="utf-8")
    assert _run(REPORT, listing).returncode == 1


# ---------------------------------------------------------------- prompt context


def test_style_is_stored_in_the_pack(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    assert "还没有设定" in _fig(pack, "style", cwd=tmp_path)[1]
    rc, _ = _fig(pack, "style", "扁平插画，  蓝绿主色", cwd=tmp_path)
    assert rc == 0
    parsed = ap.parse_pack(pack.read_text(encoding="utf-8"))
    assert parsed.front["figure-style"] == "扁平插画， 蓝绿主色"
    assert parsed.draft() == ap.parse_pack(EXAMPLE.read_text(encoding="utf-8")).draft()
    assert "已清除" in _fig(pack, "style", "", cwd=tmp_path)[1]
    assert "figure-style" not in ap.parse_pack(pack.read_text(encoding="utf-8")).front


def _context(pack: Path, pos: str, *extra: str, cwd: Path) -> dict:  # type: ignore[type-arg]
    rc, out = _fig(pack, "context", "--pos", pos, "--json", *extra, cwd=cwd)
    assert rc == 0, out
    return json.loads(out)


def test_context_reads_around_the_anchor(tmp_path: Path) -> None:
    pack = _illustrated(tmp_path)
    _fig(pack, "style", "扁平插画，蓝绿主色", cwd=tmp_path)
    ctx = _context(pack, "第4段后", cwd=tmp_path)
    assert [p["段"] for p in ctx["上下文段落"]] == [2, 3, 4, 5, 6]
    assert [p["段"] for p in ctx["上下文段落"] if p["锚点"]] == [4]
    assert ctx["所在小节"] == "规模有多大" and ctx["统一画面风格"] == "扁平插画，蓝绿主色"
    assert ctx["读者"] and ctx["标题"].startswith("新建充电桩")
    assert all("[S" not in p["原文"] for p in ctx["上下文段落"])  # citation marks stripped
    assert {e["来源"] for e in ctx["相关来源摘录"]} >= {"S2"}
    assert [f["编号"] for f in ctx["已有配图"]] == ["P1", "P2"]
    assert any("扁平插画，蓝绿主色" in line for line in ctx["提示词骨架"])
    narrow = _context(pack, "第4段后", "--window", "0", cwd=tmp_path)
    assert [p["段"] for p in narrow["上下文段落"]] == [4]


def test_context_for_cover_heading_and_bad_positions(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    cover = _context(pack, "封面", cwd=tmp_path)
    assert [p["段"] for p in cover["上下文段落"]] == [1, 2, 3, 4]
    assert len(cover["全文小标题"]) == 5 and any("2.35:1" in line for line in cover["提示词骨架"])
    section = _context(pack, "「要等多久」后", "--window", "1", cwd=tmp_path)
    assert section["所在小节"] == "要等多久"
    assert [p["段"] for p in section["上下文段落"]] == [5, 6]
    rc, out = _fig(pack, "context", "--pos", "第99段后", cwd=tmp_path)
    assert rc == 1 and "超出范围" in out
    text = _fig(pack, "context", "--pos", "第2段后", cwd=tmp_path)[1]
    assert "◀ 图放在这一段后面" in text and "提示词骨架" in text


# ---------------------------------------------------------------- tied to the article


def test_context_suggests_roles_and_shows_both_sides(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    assert _context(pack, "封面", cwd=tmp_path)["建议用途"] == "点题"
    bridge = _context(pack, "「要等多久」后", cwd=tmp_path)
    assert bridge["建议用途"] == "衔接"
    assert bridge["上文"]["范围"] == "「规模有多大」" and "2亿元" in bridge["上文"]["原文"]
    assert bridge["下文"]["范围"] == "「要等多久」" and "到账时间" in bridge["下文"]["原文"]
    assert (
        _context(pack, "「补贴多少」后", cwd=tmp_path)["建议用途"] == "衔接"
    )  # intro -> first section
    assert _context(pack, "第3段后", cwd=tmp_path)["建议用途"] == "衔接"  # section boundary
    assert any(line.startswith("0. 用途") for line in bridge["提示词骨架"])
    plan = _fig(pack, "plan", cwd=tmp_path)[1]
    assert "「要等多久」后：衔接" in plan and "封面：点题" in plan


def test_prompt_is_built_from_article_elements(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    _fig(pack, "style", "扁平插画，蓝绿主色", cwd=tmp_path)
    args = ["--pos", "「要等多久」后", "--role", "衔接", "--point", "充电桩建成后在等审批",
            "--elements", "公共充电桩,审批", "--scene", "傍晚", "--json"]  # fmt: skip
    rc, out = _fig(pack, "prompt", *args, cwd=tmp_path)
    assert rc == 0, out
    text = json.loads(out)["提示词"]
    assert "从「规模有多大」过渡到「要等多久」" in text and "公共充电桩" in text and "审批" in text
    assert "扁平插画，蓝绿主色" in text and "不出现任何文字" in text
    one_side = _fig(pack, "prompt", *args[:7], "审批", cwd=tmp_path)
    assert one_side[0] == 1 and "没有上文" in one_side[1]
    invented = _fig(pack, "prompt", "--pos", "封面", "--role", "点题", "--point", "x",
                    "--elements", "飞船", cwd=tmp_path)  # fmt: skip
    assert invented[0] == 1 and "找不到" in invented[1]
    cover = json.loads(_fig(pack, "prompt", "--pos", "封面", "--role", "点题", "--point", "补贴来了",
                            "--elements", "公共充电桩", "--json", cwd=tmp_path)[1])  # fmt: skip
    assert "公众号封面" in cover["提示词"] and "2.35:1" in cover["提示词"]


def test_add_refuses_figures_not_tied_to_the_text(tmp_path: Path) -> None:
    pack = _variant(tmp_path)
    (tmp_path / "x.jpg").write_bytes(_jpeg())
    base = [
        "--file",
        "x.jpg",
        "--mode",
        "生成",
        "--prompt",
        "p",
        "--model",
        "m",
        "--caption",
        "充电桩",
    ]
    bridge = ["--pos", "「要等多久」后", "--role", "衔接", "--point", "建成后等审批"]

    def add(*extra: str) -> tuple[int, str]:
        return _fig(pack, "add", *base, *extra, cwd=tmp_path)

    rc, out = add(*bridge, "--elements", "公共充电桩,审批", "--basis", "「凭空编的一句话」",
                  "--seen", "公共充电桩和审批窗口")  # fmt: skip
    assert rc == 1 and "不是初稿里的原文" in out
    rc, out = add(*bridge, "--elements", "公共充电桩,审批", "--basis", "「关于到账时间，官方没有给出时限」",
                  "--seen", "公共充电桩和审批窗口")  # fmt: skip
    assert rc == 1 and "没有上文" in out  # basis only quotes the next section
    rc, out = add(*bridge, "--elements", "公共充电桩,审批,申请材料",
                  "--basis", "「这笔补贴大约能带动5000个公共充电桩建成」「关于到账时间，官方没有给出时限」",
                  "--seen", "一片蓝色的天空")  # fmt: skip
    assert rc == 1 and "画面描述里没看到" in out
    rc, out = add(*bridge, "--elements", "公共充电桩,审批",
                  "--basis", "「这笔补贴大约能带动5000个公共充电桩建成」「关于到账时间，官方没有给出时限」",
                  "--seen", "左边是一排建好的公共充电桩，右边是排队等审批的商户")  # fmt: skip
    assert rc == 0, out
    fig = ap.parse_pack(pack.read_text(encoding="utf-8")).figures()[0]
    assert fig.fields["用途"] == "衔接" and fig.fields["画面元素"] == "公共充电桩，审批"
    assert "公共充电桩" in fig.fields["画面描述"]
    rc, out = _fig(pack, "check", cwd=tmp_path)
    assert rc == 0, out
    rc, out = add("--pos", "开头", "--role", "衔接", "--point", "x", "--elements", "公共充电桩",
                  "--basis", "「新建的公共充电桩」", "--seen", "公共充电桩")  # fmt: skip
    assert rc == 1 and "不能做衔接图" in out


def test_check_flags_old_figures_without_reasons(tmp_path: Path) -> None:
    pack = _illustrated(tmp_path)
    text = pack.read_text(encoding="utf-8")
    pack.write_text(
        re.sub(r"(?m)^- (用途|画面要点|依据|画面元素|画面描述)：.*\n", "", text), encoding="utf-8"
    )
    rc, out = _fig(pack, "check", cwd=tmp_path)
    assert rc == 3 and "用途应为" in out and "缺少画面描述" in out
