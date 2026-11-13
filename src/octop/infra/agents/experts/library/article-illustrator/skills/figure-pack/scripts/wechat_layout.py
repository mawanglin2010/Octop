#!/usr/bin/env python3
"""Markdown → 公众号排版 HTML（全部内联样式，复制进公众号编辑器不丢格式）。

Usage:
    python scripts/wechat_layout.py article.md -o article.html [--theme green|blue|orange]
    python scripts/wechat_layout.py article.md --stats            # 只看字数、阅读时长等

输出的 .html 在浏览器打开后点「复制正文」，到公众号后台编辑器粘贴即可。
外链在公众号正文里不可点击，会自动转成文末「参考链接」脚注；本地图片需在编辑器里重新上传。

Markdown 约定：开头可选 front matter（--- 包围，键：标题 / 作者 / 摘要 / 封面）；
每个非空行是一个段落；支持 ## ### 小标题、列表、> 引用、```代码```、表格、
![图注](图片地址)、---- 分割线、**加粗**、*斜体*、`代码`、==高亮==、[文字](链接)。
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

THEMES = {
    "green": {"primary": "#07a35a", "light": "#e8f7ef", "label": "清新绿（默认）"},
    "blue": {"primary": "#1e5bd8", "light": "#eaf1fd", "label": "商务蓝"},
    "orange": {"primary": "#d9622b", "light": "#fdf0e9", "label": "活力橙"},
}
BASE = (
    "font-size:15px;color:#333;line-height:1.75;letter-spacing:0.5px;"
    "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei',sans-serif;word-break:break-word;"
)
CHARS_PER_MINUTE = 400


def parse_front_matter(source: str) -> tuple[dict[str, str], list[str]]:
    lines = source.replace("\r\n", "\n").split("\n")
    meta: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for raw in lines[1:i]:
                    key, sep, value = raw.replace("：", ":", 1).partition(":")
                    if sep:
                        meta[key.strip()] = value.strip()
                lines = lines[i + 1 :]
                break
    return meta, lines


class Renderer:
    def __init__(self, theme: str) -> None:
        if theme not in THEMES:
            raise ValueError(f"未知主题 {theme!r}，可选：{', '.join(THEMES)}")
        self.t = THEMES[theme]
        self.links: list[tuple[str, str]] = []

    # ---- inline

    def inline(self, text: str) -> str:
        p = self.t["primary"]
        out = html.escape(text, quote=False)
        out = re.sub(
            r"`([^`]+)`",
            r'<code style="background:#f2f2f2;color:#c7254e;padding:2px 4px;'
            r'border-radius:3px;font-size:90%;">\1</code>',
            out,
        )
        out = re.sub(
            r"\*\*(.+?)\*\*", rf'<strong style="color:{p};font-weight:bold;">\1</strong>', out
        )
        out = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", out)
        out = re.sub(
            r"==(.+?)==",
            rf'<span style="background:{self.t["light"]};padding:1px 3px;">\1</span>',
            out,
        )

        def link(m: re.Match[str]) -> str:
            label, url = m.group(1), html.unescape(m.group(2))
            if url.startswith("https://mp.weixin.qq.com/"):
                return f'<a href="{html.escape(url)}" style="color:{p};">{label}</a>'
            self.links.append((html.unescape(label), url))
            return f'{label}<sup style="color:{p};font-size:12px;">[{len(self.links)}]</sup>'

        return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", link, out)

    # ---- blocks

    def p(self, text: str) -> str:
        return f'<p style="margin:0 0 16px;text-align:justify;">{self.inline(text)}</p>'

    def render(self, lines: list[str]) -> str:
        p, light = self.t["primary"], self.t["light"]
        parts: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            s = line.strip()
            if not s:
                i += 1
                continue
            if s.startswith("```"):
                body: list[str] = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    body.append(lines[i])
                    i += 1
                code = html.escape("\n".join(body))
                parts.append(
                    '<pre style="background:#f6f8fa;border-radius:6px;padding:12px;'
                    "font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-all;"
                    f'margin:0 0 16px;"><code>{code}</code></pre>'
                )
                i += 1
                continue
            if s.startswith("|") and s.endswith("|"):
                rows: list[list[str]] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        rows.append(cells)
                    i += 1
                parts.append(self.table(rows))
                continue
            if re.fullmatch(r"-{3,}|\*{3,}", s):
                parts.append('<hr style="border:none;border-top:1px dashed #ccc;margin:24px 0;">')
            elif s.startswith("### "):
                parts.append(
                    f'<h3 style="font-size:16px;font-weight:bold;color:#222;margin:24px 0 12px;">'
                    f"{self.inline(s[4:])}</h3>"
                )
            elif s.startswith("## ") or s.startswith("# "):
                text = s.split(" ", 1)[1]
                parts.append(
                    f'<h2 style="font-size:18px;font-weight:bold;color:{p};margin:32px 0 16px;'
                    f'padding-left:10px;border-left:4px solid {p};line-height:1.5;">'
                    f"{self.inline(text)}</h2>"
                )
            elif s.startswith(">"):
                quote: list[str] = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    quote.append(lines[i].strip().lstrip(">").strip())
                    i += 1
                inner = "".join(
                    f'<p style="margin:0 0 6px;">{self.inline(q)}</p>' for q in quote if q
                )
                parts.append(
                    f'<blockquote style="margin:16px 0;padding:12px 16px;background:{light};'
                    f'border-left:3px solid {p};color:#555;font-size:14px;">{inner}</blockquote>'
                )
                continue
            elif m := re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", s):
                alt, src = m.group(1), m.group(2)
                parts.append(
                    f'<p style="margin:16px 0 4px;text-align:center;"><img src="{html.escape(src)}" '
                    f'alt="{html.escape(alt)}" style="max-width:100%;border-radius:6px;"></p>'
                )
                if alt:
                    parts.append(
                        '<p style="margin:0 0 16px;text-align:center;font-size:13px;color:#999;">'
                        f"{html.escape(alt)}</p>"
                    )
            elif re.match(r"^([-*+]|\d+[.)、])\s", s):
                # 公众号编辑器对 ul/ol 支持不稳定，列表转成带悬挂缩进的段落。
                while i < len(lines) and re.match(r"^\s*([-*+]|\d+[.)、])\s", lines[i]):
                    item = lines[i].strip()
                    m2 = re.match(r"^([-*+]|\d+[.)、])\s+(.*)$", item)
                    assert m2 is not None
                    marker = "•" if m2.group(1) in "-*+" else m2.group(1)
                    parts.append(
                        f'<p style="margin:0 0 8px;padding-left:1.2em;text-indent:-1.2em;">'
                        f'<span style="color:{p};font-weight:bold;">{html.escape(marker)}</span> '
                        f"{self.inline(m2.group(2))}</p>"
                    )
                    i += 1
                continue
            else:
                parts.append(self.p(s))
            i += 1
        if self.links:
            parts.append(
                '<h3 style="font-size:14px;color:#999;margin:32px 0 8px;font-weight:normal;">参考链接</h3>'
            )
            for n, (label, url) in enumerate(self.links, start=1):
                parts.append(
                    '<p style="margin:0 0 4px;font-size:12px;color:#999;word-break:break-all;">'
                    f"[{n}] {html.escape(label)}：{html.escape(url)}</p>"
                )
        return f'<section style="{BASE}">' + "".join(parts) + "</section>"

    def table(self, rows: list[list[str]]) -> str:
        if not rows:
            return ""
        th = (
            f"border:1px solid #e5e5e5;padding:6px 8px;background:{self.t['light']};"
            "font-weight:bold;text-align:left;"
        )
        td = "border:1px solid #e5e5e5;padding:6px 8px;"
        head = "".join(f'<th style="{th}">{self.inline(c)}</th>' for c in rows[0])
        body = "".join(
            "<tr>" + "".join(f'<td style="{td}">{self.inline(c)}</td>' for c in r) + "</tr>"
            for r in rows[1:]
        )
        return (
            '<section style="overflow-x:auto;margin:0 0 16px;">'
            '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>"
        )


def stats(meta: dict[str, str], lines: list[str]) -> dict[str, int | str]:
    text = "\n".join(lines)
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)|\]\([^)]*\)|[#>*`|=\-\[\]]", "", text)
    cjk = len(re.findall(r"[一-鿿]", plain))
    words = len(re.findall(r"[A-Za-z0-9]+", plain))
    paragraphs = [
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith(("#", "|", "```", "!", "---"))
    ]
    longest = max((len(p) for p in paragraphs), default=0)
    total = cjk + words
    return {
        "字数": total,
        "预计阅读": f"{max(1, round(total / CHARS_PER_MINUTE))} 分钟",
        "段落数": len(paragraphs),
        "最长段落": longest,
        "配图数": len(re.findall(r"!\[", text)),
        "标题字数": len(meta.get("标题", "")),
        "摘要字数": len(meta.get("摘要", "")),
    }


def warnings(meta: dict[str, str], longest: int) -> list[str]:
    out = []
    title, abstract = meta.get("标题", ""), meta.get("摘要", "")
    if not title:
        out.append("front matter 未填写 标题")
    elif len(title) > 64:
        out.append(f"标题 {len(title)} 字，公众号标题上限 64 字（建议 30 字以内）")
    if len(abstract) > 120:
        out.append(f"摘要 {len(abstract)} 字，公众号摘要上限 120 字")
    if longest > 200:
        out.append(f"最长段落 {longest} 字，手机阅读建议每段不超过 150 字")
    return out


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; background:#f2f3f5; font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; }}
  .bar {{ position:sticky; top:0; background:#fff; border-bottom:1px solid #e5e5e5; padding:10px 16px;
          display:flex; gap:12px; align-items:center; flex-wrap:wrap; font-size:13px; color:#666; z-index:1; }}
  .bar button {{ background:{primary}; color:#fff; border:none; border-radius:4px; padding:8px 16px;
                 font-size:14px; cursor:pointer; }}
  .phone {{ max-width:578px; margin:16px auto; background:#fff; padding:24px 16px; }}
  .meta h1 {{ font-size:22px; line-height:1.4; margin:0 0 8px; color:#222; }}
  .meta p {{ font-size:13px; color:#999; margin:0 0 20px; }}
  .warn {{ color:#c0392b; }}
</style></head>
<body>
<div class="bar">
  <button id="copy">复制正文</button>
  <span id="msg">{stats}</span>
  {warn}
</div>
<div class="phone">
  <div class="meta"><h1>{title}</h1><p>{author}</p></div>
  <div id="article">{body}</div>
</div>
<script>
document.getElementById("copy").addEventListener("click", async () => {{
  const node = document.getElementById("article");
  const msg = document.getElementById("msg");
  try {{
    const item = new ClipboardItem({{
      "text/html": new Blob([node.innerHTML], {{ type: "text/html" }}),
      "text/plain": new Blob([node.innerText], {{ type: "text/plain" }}),
    }});
    await navigator.clipboard.write([item]);
  }} catch (e) {{
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    document.execCommand("copy");
    sel.removeAllRanges();
  }}
  msg.textContent = "已复制，去公众号编辑器粘贴（标题、作者、摘要请在后台单独填写）";
}});
</script>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input")
    parser.add_argument("-o", "--output", help="输出 .html（省略时只输出统计）")
    parser.add_argument("--theme", default="green", choices=sorted(THEMES))
    parser.add_argument("--fragment", action="store_true", help="只输出正文 <section>，不带预览页")
    parser.add_argument("--stats", action="store_true", help="只输出统计信息")
    args = parser.parse_args(argv)
    try:
        meta, lines = parse_front_matter(Path(args.input).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"无法读取：{exc}", file=sys.stderr)
        return 1
    st = stats(meta, lines)
    warns = warnings(meta, int(st["最长段落"]))
    summary = "，".join(f"{k} {v}" for k, v in st.items())
    if args.stats or not args.output:
        print(summary)
        for w in warns:
            print(f"提示：{w}")
        return 0
    body = Renderer(args.theme).render(lines)
    if args.fragment:
        page = body
    else:
        title = meta.get("标题", "")
        page = PAGE.format(
            title=html.escape(title or "公众号预览"),
            author=html.escape(meta.get("作者", "")),
            primary=THEMES[args.theme]["primary"],
            stats=html.escape(summary),
            warn="".join(f'<span class="warn">{html.escape(w)}</span>' for w in warns),
            body=body,
        )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"已生成 {out}（{summary}）")
    for w in warns:
        print(f"提示：{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
