#!/usr/bin/env python3
"""把文章包（初稿 + 配图单）排成预览页，导出 PDF 给用户预览；同时留一份 HTML。

Usage:
    python scripts/preview_pdf.py 文章包.md [-o outputs/preview/<话题>.pdf] [--theme green|blue|orange]
                                  [--author 作者] [--base .] [--html-only]

- 正文按公众号排版（和 wechat_layout.py 同一套样式），去掉 [S1]、[推算] 标记，配图按“位置”插入，封面放在最前；
- 页首写明“预览稿，未发布”、核验结论；文末列参考来源、图片来源和配图清单；
- PDF 用无头 Chromium 打印（Octop 设置 → 浏览器里安装，或 `playwright install chromium`）。
  没有 Chromium 时只生成 HTML，退出码 2，HTML 可以直接用浏览器打开预览。
Exit codes: 0 成功, 2 缺少 Chromium（已生成 HTML）, 1 输入错误。
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import article_pack as ap  # noqa: E402
import wechat_layout as wl  # noqa: E402

MARK_RE = re.compile(r"\[(?:S\d+(?:\s*[,，、]\s*S\d+)*|推算)\]")
CJK_FONTS = (
    "'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC','Noto Sans SC',"
    "'Source Han Sans SC','WenQuanYi Micro Hei','WenQuanYi Zen Hei','Droid Sans Fallback',sans-serif"
)
PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  body {{ margin:0; font-family:{fonts}; color:#333; }}
  .doc {{ max-width:600px; margin:0 auto; }}
  .stamp {{ font-size:12px; color:#8a6d3b; background:#fcf8e3; border:1px solid #faebcc;
            border-radius:4px; padding:6px 10px; margin-bottom:16px; }}
  h1.title {{ font-size:24px; line-height:1.4; margin:0 0 8px; color:#222; }}
  .meta {{ font-size:13px; color:#999; margin:0 0 16px; }}
  .digest {{ font-size:14px; color:#666; background:#f7f7f7; padding:8px 12px; margin:0 0 16px; }}
  .cover img {{ width:100%; display:block; border-radius:4px; }}
  .cover p {{ font-size:12px; color:#999; text-align:center; margin:4px 0 16px; }}
  img {{ max-width:100%; page-break-inside:avoid; }}
  .appendix {{ margin-top:28px; border-top:1px solid #eee; padding-top:12px; font-size:12px; color:#666; }}
  .appendix h2 {{ font-size:14px; color:#333; margin:12px 0 6px; }}
  .appendix li {{ margin:2px 0; word-break:break-all; }}
  table {{ border-collapse:collapse; width:100%; }}
  td, th {{ border:1px solid #ddd; padding:4px 6px; text-align:left; vertical-align:top; }}
  td:nth-child(-n+3), th:nth-child(-n+3), td:nth-child(5), th:nth-child(5) {{ white-space:nowrap; }}
</style></head>
<body><div class="doc">
<div class="stamp">预览稿，未发布 · 生成于 {now} · 核验结论：{verdict}</div>
<h1 class="title">{title}</h1>
<p class="meta">{author}</p>
{digest}
{cover}
{body}
<div class="appendix">{appendix}</div>
</div></body></html>
"""


def split_draft(draft: str) -> tuple[str, str, list[str]]:
    title, digest, body = "", "", []
    for line in draft.splitlines():
        s = line.strip()
        if not title and re.match(r"^#\s+\S", s):
            title = s.lstrip("# ").strip()
            continue
        m = re.match(r"^(?:摘要|导语)\s*[：:]\s*(.*)$", s)
        if m and not digest:
            digest = m.group(1).strip()
            continue
        if ap.REF_HEAD_RE.match(s):
            break
        body.append(line)
    return title, digest, body


def clean(text: str) -> str:
    return re.sub(r"\s+([。！？，；：、）])", r"\1", MARK_RE.sub("", text))


def build_html(pack: ap.Pack, base: Path, theme: str, author: str) -> tuple[str, list[str]]:
    """Return (page html, warnings)."""
    warns: list[str] = []
    figures = pack.figures()

    def src_for(fig: ap.Figure) -> str | None:
        p = (base / fig.fields.get("文件", "")).resolve()
        if not fig.fields.get("文件") or not p.is_file():
            warns.append(f"{fig.id} 的图片文件不存在：{fig.fields.get('文件', '')}，预览里跳过")
            return None
        return p.as_uri()

    title, digest, body = split_draft(pack.draft())
    lines = ap.illustrated_lines(
        "\n".join(body), [f for f in figures if f.position != "封面"], src_for
    )
    _meta, parsed = wl.parse_front_matter("\n".join(clean(x) for x in lines))
    body_html = wl.Renderer(theme).render(parsed)

    cover_html = ""
    for fig in figures:
        if fig.position == "封面":
            src = src_for(fig)
            if src:
                cover_html = (
                    f'<div class="cover"><img src="{html.escape(src)}" alt="">'
                    f"<p>封面：{html.escape(fig.caption())}</p></div>"
                )
    by_id = {s.id: s for s in pack.sources()}
    refs = [by_id[i] for i in ap.citation_ids(pack.draft()) if i in by_id]
    parts = []
    if refs:
        items = "".join(
            f"<li>{html.escape(s.fields.get('发布方', ''))}《{html.escape(s.fields.get('标题', ''))}》，"
            f"{html.escape(s.fields.get('发布日期', '未知'))}，{html.escape(s.fields.get('网址', ''))}</li>"
            for s in refs
        )
        parts.append(f"<h2>参考来源</h2><ul>{items}</ul>")
    credits = ap.figure_credits(figures)
    if credits:
        parts.append(
            "<h2>图片来源</h2><ul>"
            + "".join(f"<li>{html.escape(c)}</li>" for c in credits)
            + "</ul>"
        )
    if figures:
        rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{html.escape(v)}</td>"
                for v in (
                    f.id,
                    f.position,
                    f.fields.get("用途", ""),
                    f.fields.get("画面要点", ""),
                    f.mode,
                    f.fields.get("授权") or f.fields.get("模型", ""),
                    f.fields.get("共享名", ""),
                )
            )
            + "</tr>"
            for f in figures
        )
        parts.append(
            "<h2>配图清单</h2><table><tr><th>编号</th><th>位置</th><th>用途</th><th>画面要点</th><th>方式</th>"
            f"<th>授权 / 模型</th><th>共享名</th></tr>{rows}</table>"
        )
    verdict = ap.report_fields(pack).get("结论") or "未核验"
    if ap.report_current(pack) is False:
        verdict += "（已过期：初稿在核验后改过）"
    page = PAGE.format(
        title=html.escape(title or pack.topic),
        fonts=CJK_FONTS,
        now=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        verdict=html.escape(verdict),
        author=html.escape(author),
        digest=f'<p class="digest">{html.escape(clean(digest))}</p>' if digest else "",
        cover=cover_html,
        body=body_html,
        appendix="".join(parts),
    )
    return page, warns


def has_cjk_font() -> bool | None:
    """True/False when fontconfig can tell, None when it cannot (Windows, macOS without fc-list)."""
    fc = shutil.which("fc-list")
    if not fc:
        return None
    try:
        out = subprocess.run([fc, ":lang=zh", "family"], capture_output=True, text=True, timeout=20)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip())


SYSTEM_BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
)


def print_pdf(html_path: Path, pdf_path: Path) -> str | None:
    """Print *html_path* to *pdf_path*; returns an error message when no Chromium works.

    Tries Playwright's own Chromium first, then a system Chrome / Chromium on PATH.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return "没有安装 Playwright"
    candidates: list[str | None] = [
        None,
        *dict.fromkeys(filter(None, map(shutil.which, SYSTEM_BROWSERS))),
    ]
    last = ""
    with sync_playwright() as pw:
        for exe in candidates:
            try:
                browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
            except PlaywrightError as exc:
                last = str(exc)
                continue
            try:
                page = browser.new_page()
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                page.pdf(path=str(pdf_path), format="A4", print_background=True,
                         margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})  # fmt: skip
                return None
            except PlaywrightError as exc:
                return f"Chromium 打印失败：{str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
            finally:
                browser.close()
    if "Executable doesn't exist" in last or "playwright install" in last:
        return (
            "Chromium 未安装：在 Octop 设置 → 浏览器 中安装，或运行 `playwright install chromium`"
        )
    return f"Chromium 启动失败：{last.splitlines()[0] if last else '未知原因'}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="文章包预览 PDF")
    p.add_argument("pack")
    p.add_argument("-o", "--output", default="")
    p.add_argument("--theme", default="green", choices=sorted(wl.THEMES))
    p.add_argument("--author", default="")
    p.add_argument("--base", default=".", help="配图单里“文件”路径相对的目录")
    p.add_argument("--html-only", action="store_true")
    a = p.parse_args(argv)
    try:
        pack = ap.read_pack(Path(a.pack))
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)
        return 1
    if not pack.draft():
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    pdf = Path(a.output or f"outputs/preview/{ap.slugify(pack.topic or Path(a.pack).stem)}.pdf")
    if pdf.suffix.lower() != ".pdf":
        pdf = pdf.with_suffix(".pdf")
    page, warns = build_html(pack, Path(a.base), a.theme, a.author)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    html_path = pdf.with_suffix(".html")
    html_path.write_text(page, encoding="utf-8")
    for w in warns:
        print(f"提示：{w}")
    if a.html_only:
        print(f"已生成 {html_path}")
        return 0
    if has_cjk_font() is False:
        print("提示：这台机器没有找到中文字体（fc-list :lang=zh 为空），PDF 里的中文可能显示成方框；"
              "可安装 fonts-noto-cjk 或文泉驿字体后重试。HTML 版不受影响。")  # fmt: skip
    problem = print_pdf(html_path, pdf)
    if problem:
        print(f"{problem}。已生成 HTML 预览：{html_path}（用浏览器打开即可）")
        return 2
    print(f"已生成 {pdf}（{pdf.stat().st_size} 字节）和 {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
