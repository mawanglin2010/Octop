#!/usr/bin/env python3
"""文章包：内容团队各成员之间交接的统一文本格式，以及发布前的把关。

成员的工作区互相看不见，主持人只能转述文字，所以每一站都把成果写进同一份“文章包”，下一站接着读：
    选题单 → 线索包 → 初稿 → 核验报告 → 配图单 → 发布单
一个文章包只写一个话题：用户给了多个热点或主题时，每个各建一个文章包、各写一篇（batch 一次建好）。
本脚本在内容采集员、公众号写作专家、内容核验员、推文配图专家、发布专员处各存一份，内容完全相同（有测试保证）。

Usage:
    python scripts/article_pack.py new 文章包.md --topic 话题 [--platform 公众号] [--audience ..] [--length ..] [--style ..] [--avoid ..]
    python scripts/article_pack.py batch 目录 --topic 话题1 --topic 话题2 [...同 new 的选项]   # 每个话题一个文章包
    python scripts/article_pack.py show 文章包.md [--json]
    python scripts/article_pack.py get 文章包.md 初稿 [-o 初稿.md]
    python scripts/article_pack.py put 文章包.md 线索包 --file 线索.md      # 整节替换；- 表示读标准输入
    python scripts/article_pack.py hash 文章包.md [--section 初稿]
    python scripts/article_pack.py validate 文章包.md [--json]
    python scripts/article_pack.py gate 文章包.md --for publish [--confirmed] [--json]

线索包里每条来源是一个 “### S1” 小节：
    - 标题：…   - 网址：https://…   - 发布方：…   - 发布日期：2026-11-01（不知道写“未知”）
    - 抓取时间：2026-11-05T10:00:00+08:00   - 可信度：A|B|C|D   - 状态：可用|存疑|不可用
    - 摘录：
      > 原文逐字摘录（核验时只认这里的文字）
初稿里的事实在句末用 [S1]、[S1,S2] 标来源；核验报告用“核验对象：sha256:…”记录核验的是哪一版初稿。

配图单里每张图是一个 “### P1” 小节（配图不改初稿，渲染时按“位置”插入，所以核验报告不会因此过期）：
    - 位置：封面 | 开头 | 第3段后 | 「小标题文字」后       （“段”= 标题以下每个正文段落，plan 命令会列出编号）
    - 方式：搜索 | 生成
    - 文件：images/<话题>/P1.jpg（配图专家工作区里的路径）   - 共享名：<话题>/P1.jpg（共享文件夹里的名字）
    - 哈希：sha256:…（文件内容）                            - 图注：…
    搜索来的图还要：网址（图片地址）、来源页、署名、授权（如 CC BY 4.0、官方发布允许转载、已获授权、公有领域）；
    生成的图还要：提示词、模型；图注渲染时自动加“（AI 生成）”。

可信度：A 一手来源（官方公告、原始文件、论文）；B 主流媒体或权威机构的转述；C 自媒体、论坛、单一来源；D 无法核实、匿名传言。

Exit codes: 0 正常, 3 validate / gate 未通过, 1 输入错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SECTIONS = ["选题单", "线索包", "初稿", "核验报告", "配图单", "发布单"]
FRONT_KEY = "article-pack"
FRONT_VERSION = "1"
CREDIBILITY = ("A", "B", "C", "D")
SOURCE_STATUS = ("可用", "存疑", "不可用")
VERDICTS = ("通过", "需修改", "不通过")
PUBLISH_STATES = ("待确认", "已存草稿", "已发布", "失败")
CITE_RE = re.compile(r"\[(S\d+(?:\s*[,，、]\s*S\d+)*)\]")
FIELD_RE = re.compile(r"^\s*[-*]\s*([^：:\n]{1,12}?)\s*[：:]\s*(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
SOURCE_HEAD_RE = re.compile(r"^###\s+(S\d+)\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_SOURCE_FIELDS = ("标题", "网址", "发布日期", "抓取时间", "可信度", "状态")
FIGURE_HEAD_RE = re.compile(r"^###\s+(P\d+)\s*$")
FIGURE_MODES = ("搜索", "生成")
REQUIRED_FIGURE_FIELDS = ("位置", "方式", "文件", "共享名", "哈希", "图注")
SEARCH_FIGURE_FIELDS = ("网址", "来源页", "署名", "授权")
GENERATED_FIGURE_FIELDS = ("提示词", "模型")
UNKNOWN_LICENSE = ("", "未知", "不明", "不详", "无", "待确认")
AI_LABEL = "（AI 生成）"
POS_PARA_RE = re.compile(r"^第\s*(\d+)\s*段后$")
POS_HEAD_RE = re.compile(r"^[「“\"](.+?)[」”\"]\s*后$")
SKIP_PARA_RE = re.compile(r"^(?:#|!\[|>|\||-{3,}\s*$|```|(?:摘要|导语)\s*[：:])")
REF_HEAD_RE = re.compile(r"^#{2,6}\s*(?:参考来源|参考资料|参考链接)\s*$")


@dataclass
class Source:
    id: str
    fields: dict[str, str] = field(default_factory=dict)
    excerpt: str = ""

    @property
    def status(self) -> str:
        return self.fields.get("状态", "")

    @property
    def credibility(self) -> str:
        return self.fields.get("可信度", "")


@dataclass
class Figure:
    id: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def position(self) -> str:
        return self.fields.get("位置", "")

    @property
    def mode(self) -> str:
        return self.fields.get("方式", "")

    def caption(self) -> str:
        text = self.fields.get("图注", "")
        if self.mode == "生成" and AI_LABEL not in text:
            text = f"{text}{AI_LABEL}"
        return text


@dataclass
class Pack:
    front: dict[str, str]
    sections: dict[str, str]

    @property
    def topic(self) -> str:
        return self.front.get("topic", "")

    def sources(self) -> list[Source]:
        return parse_sources(self.sections.get("线索包", ""))

    def draft(self) -> str:
        return self.sections.get("初稿", "")

    def figures(self) -> list[Figure]:
        return parse_figures(self.sections.get("配图单", ""))


@dataclass
class Issue:
    level: str  # error | warn
    where: str
    message: str


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().splitlines())


def text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:12]


def file_hash(data: bytes) -> str:
    """Hash of an image file, as recorded in the 配图单 (“哈希：sha256:…”)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()[:12]


def citation_ids(text: str) -> list[str]:
    """Source ids cited in *text*, in order of first appearance."""
    seen: list[str] = []
    for m in CITE_RE.finditer(text):
        for sid in re.findall(r"S\d+", m.group(1)):
            if sid not in seen:
                seen.append(sid)
    return seen


def parse_fields(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        m = FIELD_RE.match(line)
        if m and m.group(1).strip() not in out:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def parse_sources(text: str) -> list[Source]:
    sources: list[Source] = []
    cur: Source | None = None
    body: list[str] = []

    def flush() -> None:
        if cur is None:
            return
        cur.fields = parse_fields(body)
        quotes: list[str] = []
        in_excerpt = False
        for line in body:
            m = FIELD_RE.match(line)
            if m:
                in_excerpt = m.group(1).strip() == "摘录"
                inline = m.group(2).strip()
                if in_excerpt and inline:
                    quotes.append(inline.lstrip("> ").strip())
                continue
            q = QUOTE_RE.match(line)
            if in_excerpt and q:
                quotes.append(q.group(1).strip())
            elif line.strip() and not q:
                in_excerpt = False
        cur.excerpt = "\n".join(x for x in quotes if x)
        sources.append(cur)

    for line in text.splitlines():
        head = SOURCE_HEAD_RE.match(line)
        if head:
            flush()
            cur, body = Source(head.group(1)), []
        elif cur is not None:
            body.append(line)
    flush()
    return sources


def parse_figures(text: str) -> list[Figure]:
    figures: list[Figure] = []
    cur: Figure | None = None
    body: list[str] = []
    for line in text.splitlines() + ["### P0"]:
        head = FIGURE_HEAD_RE.match(line)
        if head:
            if cur is not None:
                cur.fields = parse_fields(body)
                figures.append(cur)
            cur, body = Figure(head.group(1)), []
        elif cur is not None:
            body.append(line)
    return figures


def slugify(topic: str, limit: int = 40) -> str:
    """File-system friendly name for a topic (keeps Chinese, drops path and shell characters)."""
    out = re.sub(
        r"[\s/\\:*?\"<>|#%&{}$!'`@+=,;\[\]()（）【】《》，。、；：！？“”‘’]+", "-", topic.strip()
    )
    out = re.sub(r"-{2,}", "-", out).strip("-.")
    return out[:limit].rstrip("-.") or "topic"


def paragraphs(draft: str) -> list[str]:
    """Body paragraphs of a draft, numbered from 1 by figure positions (“第N段后”)."""
    out: list[str] = []
    seen_title = False
    for line in draft.splitlines():
        s = line.strip()
        if not s:
            continue
        if not seen_title and re.match(r"^#\s+\S", s):
            seen_title = True
            continue
        if REF_HEAD_RE.match(s):
            break
        if SKIP_PARA_RE.match(s):
            continue
        out.append(s)
    return out


def headings(draft: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", draft)]


def position_error(pos: str, draft: str) -> str | None:
    """Why *pos* is not a valid figure position for *draft* (None when it is)."""
    if pos in ("封面", "开头"):
        return None
    m = POS_PARA_RE.match(pos)
    if m:
        n, total = int(m.group(1)), len(paragraphs(draft))
        if draft and not 1 <= n <= total:
            return f"位置“{pos}”超出范围：初稿正文共 {total} 段"
        return None
    m = POS_HEAD_RE.match(pos)
    if m:
        if draft and m.group(1).strip() not in headings(draft):
            return f"位置“{pos}”：初稿里没有这个小标题"
        return None
    return f"位置“{pos}”应为 封面、开头、第N段后 或 「小标题」后"


def illustrated_lines(draft: str, figures: list[Figure], src_for) -> list[str]:  # type: ignore[no-untyped-def]
    """Draft lines with ``![图注](src)`` inserted at each figure's position (cover excluded).

    ``src_for(figure)`` returns the image address to use, or None to skip that figure.
    """
    by_para: dict[int, list[Figure]] = {}
    by_head: dict[str, list[Figure]] = {}
    lead: list[Figure] = []
    for fig in figures:
        pos = fig.position
        if pos == "开头":
            lead.append(fig)
        elif m := POS_PARA_RE.match(pos):
            by_para.setdefault(int(m.group(1)), []).append(fig)
        elif m := POS_HEAD_RE.match(pos):
            by_head.setdefault(m.group(1).strip(), []).append(fig)

    def img(figs: list[Figure]) -> list[str]:
        out = []
        for fig in figs:
            src = src_for(fig)
            if src:
                out.append(f"![{fig.caption()}]({src})")
        return out

    out: list[str] = []
    seen_title = False
    para = 0
    in_refs = False
    for line in draft.splitlines():
        out.append(line)
        s = line.strip()
        if not s or in_refs:
            continue
        if not seen_title and re.match(r"^#\s+\S", s):
            seen_title = True
            out.extend(img(lead))
            continue
        if REF_HEAD_RE.match(s):
            in_refs = True
            continue
        head = re.match(r"^#{2,6}\s+(.+?)\s*$", s)
        if head:
            out.extend(img(by_head.get(head.group(1).strip(), [])))
            continue
        if SKIP_PARA_RE.match(s):
            continue
        para += 1
        out.extend(img(by_para.get(para, [])))
    if not seen_title:
        out[0:0] = img(lead)
    return out


def figure_credits(figures: list[Figure]) -> list[str]:
    """“图片来源” lines for searched images (generated ones are labelled in their captions)."""
    out = []
    for fig in figures:
        if fig.mode == "搜索":
            f = fig.fields
            out.append(f"{fig.id}：{f.get('署名', '')}，{f.get('授权', '')}，{f.get('来源页', '')}")
    return out


def parse_pack(text: str) -> Pack:
    lines = text.replace("\r\n", "\n").split("\n")
    front: dict[str, str] = {}
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for raw in lines[1:j]:
                    k, sep, v = raw.partition(":")
                    if sep:
                        front[k.strip()] = v.strip()
                i = j + 1
                break
    sections: dict[str, list[str]] = {}
    cur: str | None = None
    fence = False
    for line in lines[i:]:
        if line.lstrip().startswith("```"):
            fence = not fence
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and not fence and m.group(1) in SECTIONS:
            cur = m.group(1)
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(line)
    return Pack(front, {k: normalize("\n".join(v)) for k, v in sections.items()})


def render_pack(pack: Pack) -> str:
    head = ["---"] + [f"{k}: {v}" for k, v in pack.front.items()] + ["---", ""]
    head.append(f"# 文章包：{pack.topic}")
    parts = ["\n".join(head)]
    for name in SECTIONS:
        if name in pack.sections:
            parts.append(f"## {name}\n\n{pack.sections[name]}".rstrip())
    return "\n\n".join(parts) + "\n"


def read_pack(path: Path) -> Pack:
    if not path.is_file():
        raise SystemExit(f"找不到文章包：{path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise SystemExit("文章包不是 UTF-8 编码") from None
    return parse_pack(text)


def write_pack(path: Path, pack: Pack) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_pack(pack), encoding="utf-8")


def report_fields(pack: Pack) -> dict[str, str]:
    return parse_fields(pack.sections.get("核验报告", "").splitlines())


def report_current(pack: Pack) -> bool | None:
    """True when the review report was written against the draft that is in the pack now."""
    if "核验报告" not in pack.sections or "初稿" not in pack.sections:
        return None
    return report_fields(pack).get("核验对象", "") == text_hash(pack.draft())


def validate(pack: Pack) -> list[Issue]:
    issues: list[Issue] = []

    def err(where: str, msg: str) -> None:
        issues.append(Issue("error", where, msg))

    def warn(where: str, msg: str) -> None:
        issues.append(Issue("warn", where, msg))

    if pack.front.get(FRONT_KEY) != FRONT_VERSION:
        err("文件头", f"缺少 “{FRONT_KEY}: {FRONT_VERSION}”，这不是文章包（用 new 创建）")
    if not pack.topic:
        warn("文件头", "没有写 topic")
    present = [s for s in SECTIONS if s in pack.sections]
    if not present:
        err("结构", "一节都没有")
    if "选题单" not in pack.sections:
        warn("选题单", "缺少选题单，后面的成员不知道读者、平台和篇幅")

    sources = pack.sources()
    by_id: dict[str, Source] = {}
    urls: dict[str, str] = {}
    for s in sources:
        if s.id in by_id:
            err(s.id, "来源编号重复")
        by_id[s.id] = s
        for key in REQUIRED_SOURCE_FIELDS:
            if not s.fields.get(key):
                err(s.id, f"缺少“{key}”")
        if s.fields.get("网址") and not re.match(r"^https?://", s.fields["网址"]):
            err(s.id, "网址必须以 http:// 或 https:// 开头")
        date = s.fields.get("发布日期", "")
        if date and date != "未知" and not DATE_RE.match(date):
            err(s.id, f"发布日期“{date}”应为 YYYY-MM-DD 或“未知”")
        if s.credibility and s.credibility not in CREDIBILITY:
            err(s.id, f"可信度“{s.credibility}”应为 {'、'.join(CREDIBILITY)}")
        if s.status and s.status not in SOURCE_STATUS:
            err(s.id, f"状态“{s.status}”应为 {'、'.join(SOURCE_STATUS)}")
        if s.status in ("可用", "存疑") and not s.excerpt:
            err(s.id, "没有摘录：核验只认摘录里的原文，没有摘录的来源不能作为依据")
        url = s.fields.get("网址", "").split("#")[0].rstrip("/")
        if url:
            if url in urls:
                warn(s.id, f"网址和 {urls[url]} 相同，可能重复")
            urls[url] = s.id

    draft = pack.draft()
    if "初稿" in pack.sections:
        if not re.search(r"(?m)^#\s+\S", draft):
            err("初稿", "没有一级标题（以“# ”开头的一行）")
        cited = citation_ids(draft)
        if sources and not cited:
            warn("初稿", "有线索包，但初稿里一个来源编号都没标")
        for sid in cited:
            src = by_id.get(sid)
            if src is None:
                err("初稿", f"引用了不存在的来源 [{sid}]")
            elif src.status == "不可用":
                err("初稿", f"引用了状态为“不可用”的来源 [{sid}]")
            elif src.status == "存疑":
                warn("初稿", f"引用了状态为“存疑”的来源 [{sid}]，需在核验报告里说明")
            elif src.credibility == "D":
                warn("初稿", f"引用了可信度为 D 的来源 [{sid}]")

    if "核验报告" in pack.sections:
        rep = report_fields(pack)
        if rep.get("结论") not in VERDICTS:
            err("核验报告", f"结论“{rep.get('结论', '')}”应为 {'、'.join(VERDICTS)}")
        if not rep.get("核验对象", "").startswith("sha256:"):
            err("核验报告", "没有“核验对象：sha256:…”，无法知道核验的是哪一版初稿")
        elif report_current(pack) is False:
            err("核验报告", "初稿在核验之后改过，核验报告已过期，需要重新核验")

    if "配图单" in pack.sections:
        figures = pack.figures()
        if not figures:
            err("配图单", "配图单里一张图都没有（每张图是一个 “### P1” 小节）")
        ids: set[str] = set()
        covers = 0
        for fig in figures:
            f = fig.fields
            if fig.id in ids:
                err(fig.id, "配图编号重复")
            ids.add(fig.id)
            for key in REQUIRED_FIGURE_FIELDS:
                if not f.get(key):
                    err(fig.id, f"缺少“{key}”")
            if fig.mode and fig.mode not in FIGURE_MODES:
                err(fig.id, f"方式“{fig.mode}”应为 {'、'.join(FIGURE_MODES)}")
            extra = SEARCH_FIGURE_FIELDS if fig.mode == "搜索" else GENERATED_FIGURE_FIELDS
            for key in extra if fig.mode in FIGURE_MODES else ():
                if not f.get(key):
                    err(fig.id, f"{fig.mode}的图缺少“{key}”")
            if fig.mode == "搜索":
                if f.get("授权", "").strip() in UNKNOWN_LICENSE:
                    err(fig.id, "授权不明的图不能用：换一张能确认授权的图，或改用生成")
                for key in ("网址", "来源页"):
                    if f.get(key) and not re.match(r"^https?://", f[key]):
                        err(fig.id, f"{key}必须以 http:// 或 https:// 开头")
            if f.get("哈希") and not f["哈希"].startswith("sha256:"):
                err(fig.id, "哈希应为 sha256:…（用 figures.py add 登记会自动算）")
            if fig.position:
                problem = position_error(fig.position, draft)
                if problem:
                    err(fig.id, problem)
            covers += fig.position == "封面"
            if re.search(r"\d", f.get("图注", "")):
                warn(fig.id, "图注里有数字：图注只描述画面，数字要来自初稿并经过核验")
        if covers > 1:
            err("配图单", f"有 {covers} 张封面，只能有一张")
        elif covers == 0:
            warn("配图单", "没有封面（位置写“封面”）；存公众号草稿时必须有封面")

    if "发布单" in pack.sections:
        state = parse_fields(pack.sections["发布单"].splitlines()).get("状态", "")
        if state not in PUBLISH_STATES:
            err("发布单", f"状态“{state}”应为 {'、'.join(PUBLISH_STATES)}")
    return issues


def gate(pack: Pack, confirmed: bool) -> tuple[bool, list[str]]:
    """Pre-publish check. Returns (ok, reasons it is not ok)."""
    reasons = [f"{i.where}：{i.message}" for i in validate(pack) if i.level == "error"]
    if "初稿" not in pack.sections:
        reasons.append("没有初稿")
    rep = report_fields(pack) if "核验报告" in pack.sections else {}
    if not rep:
        reasons.append("没有核验报告，未经核验的稿子不能发布")
    elif rep.get("结论") != "通过":
        reasons.append(f"核验结论是“{rep.get('结论', '空')}”，只有“通过”才能发布")
    if not confirmed:
        reasons.append("还没有得到用户的明确确认（用户在对话里说“确认发布”后，再加 --confirmed）")
    return (not reasons), reasons


def summary(pack: Pack) -> dict[str, object]:
    sources = pack.sources()
    rep = report_fields(pack)
    status: dict[str, int] = {}
    for s in sources:
        status[s.status or "未填"] = status.get(s.status or "未填", 0) + 1
    draft = pack.draft()
    return {
        "话题": pack.topic,
        "已有章节": [s for s in SECTIONS if s in pack.sections],
        "来源数": len(sources),
        "来源状态": status,
        "初稿字数": len(re.sub(r"\s", "", draft)),
        "初稿哈希": text_hash(draft) if draft else None,
        "引用的来源": citation_ids(draft),
        "配图数": len(pack.figures()),
        "核验结论": rep.get("结论"),
        "核验轮次": rep.get("轮次"),
        "核验对应当前初稿": report_current(pack),
        "发布状态": parse_fields(pack.sections.get("发布单", "").splitlines()).get("状态"),
    }


def render_summary(info: dict[str, object]) -> str:
    cur = info["核验对应当前初稿"]
    stale = "" if cur is None else ("（对应当前初稿）" if cur else "（已过期，初稿改过）")
    out = [
        f"话题：{info['话题']}",
        f"已有章节：{' → '.join(info['已有章节']) or '无'}",  # type: ignore[arg-type]
        f"来源：{info['来源数']} 条 {info['来源状态'] or ''}",
        f"初稿：{info['初稿字数']} 字，哈希 {info['初稿哈希'] or '-'}；引用 {len(info['引用的来源'])} 条来源",  # type: ignore[arg-type]
        f"配图：{info['配图数']} 张",
        f"核验：{info['核验结论'] or '未核验'}{stale}"
        + (f"，第 {info['核验轮次']} 轮" if info["核验轮次"] else ""),
        f"发布：{info['发布状态'] or '未发布'}",
    ]
    return "\n".join(out)


def new_pack(a: argparse.Namespace, topic: str) -> Pack:
    brief = [
        f"- 话题：{topic}",
        f"- 目标平台：{a.platform}",
        f"- 读者：{a.audience or '（待补充）'}",
        f"- 篇幅：{a.length or '（待补充）'}",
        f"- 风格：{a.style or '（待补充）'}",
        f"- 禁区：{a.avoid or '无'}",
    ]
    return Pack(
        {
            FRONT_KEY: FRONT_VERSION,
            "topic": topic,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        {"选题单": "\n".join(brief)},
    )


def cmd_new(a: argparse.Namespace) -> int:
    path = Path(a.path)
    if path.exists() and not a.force:
        print(f"{path} 已存在，不会覆盖（要重建加 --force）", file=sys.stderr)
        return 1
    if not a.topic.strip():
        print("话题不能为空", file=sys.stderr)
        return 1
    write_pack(path, new_pack(a, a.topic.strip()))
    print(path)
    return 0


def cmd_batch(a: argparse.Namespace) -> int:
    """One pack per topic: several hot topics or themes become several articles, never one."""
    topics = list(dict.fromkeys(t.strip() for t in a.topic if t.strip()))
    if not topics:
        print("至少给一个 --topic", file=sys.stderr)
        return 1
    root = Path(a.dir)
    paths: list[Path] = []
    for topic in topics:
        base = slugify(topic)
        path, n = root / f"{base}.md", 2
        while path in paths:
            path, n = root / f"{base}-{n}.md", n + 1
        if path.exists() and not a.force:
            print(f"{path} 已存在，不会覆盖（要重建加 --force）", file=sys.stderr)
            return 1
        paths.append(path)
    for topic, path in zip(topics, paths, strict=True):
        write_pack(path, new_pack(a, topic))
        print(f"{path}\t{topic}")
    print(f"共 {len(paths)} 个文章包：每个话题单独采集、写作、核验、配图，各出一篇")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    info = summary(read_pack(Path(a.path)))
    print(json.dumps(info, ensure_ascii=False, indent=2) if a.json else render_summary(info))
    return 0


def section_name(raw: str) -> str:
    if raw not in SECTIONS:
        raise SystemExit(f"章节名只能是：{'、'.join(SECTIONS)}")
    return raw


def cmd_get(a: argparse.Namespace) -> int:
    pack = read_pack(Path(a.path))
    name = section_name(a.section)
    if name not in pack.sections:
        print(f"文章包里还没有“{name}”", file=sys.stderr)
        return 1
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(pack.sections[name] + "\n", encoding="utf-8")
        print(a.output)
    else:
        print(pack.sections[name])
    return 0


def cmd_put(a: argparse.Namespace) -> int:
    path = Path(a.path)
    name = section_name(a.section)
    if a.file == "-":
        body = sys.stdin.read()
    else:
        try:
            body = Path(a.file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"读取 {a.file} 失败：{exc}", file=sys.stderr)
            return 1
    if not body.strip():
        print("内容是空的", file=sys.stderr)
        return 1
    pack = read_pack(path)
    if not pack.front:
        print("目标文件不是文章包（缺少文件头），请先用 new 创建", file=sys.stderr)
        return 1
    body = re.sub(
        rf"(?m)^##\s+{re.escape(name)}\s*\n", "", body, count=1
    )  # tolerate a pasted heading
    pack.sections[name] = normalize(body)
    write_pack(path, pack)
    print(f"已写入“{name}”（{len(pack.sections[name])} 字符）")
    if name == "初稿" and "核验报告" in pack.sections and report_current(pack) is False:
        print("注意：初稿和核验报告对不上，需要重新核验后才能发布")
    return 0


def cmd_hash(a: argparse.Namespace) -> int:
    pack = read_pack(Path(a.path))
    name = section_name(a.section)
    if name not in pack.sections:
        print(f"文章包里还没有“{name}”", file=sys.stderr)
        return 1
    print(text_hash(pack.sections[name]))
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    issues = validate(read_pack(Path(a.path)))
    errors = [i for i in issues if i.level == "error"]
    if a.json:
        print(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=2))
    else:
        for i in issues:
            print(f"[{'错误' if i.level == 'error' else '提示'}] {i.where}：{i.message}")
        print(
            f"共 {len(errors)} 个错误，{len(issues) - len(errors)} 个提示"
            if issues
            else "文章包结构没有问题"
        )
    return 3 if errors else 0


def cmd_gate(a: argparse.Namespace) -> int:
    if a.for_ != "publish":
        print("目前只支持 --for publish", file=sys.stderr)
        return 1
    ok, reasons = gate(read_pack(Path(a.path)), a.confirmed)
    if a.json:
        print(json.dumps({"可以发布": ok, "原因": reasons}, ensure_ascii=False, indent=2))
    elif ok:
        print("可以发布：核验通过，初稿未改动，用户已确认")
    else:
        print("不能发布：")
        for r in reasons:
            print(f"- {r}")
    return 0 if ok else 3


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="文章包")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("path")
    n.add_argument("--topic", required=True)
    n.add_argument("--platform", default="微信公众号")
    n.add_argument("--audience", default="")
    n.add_argument("--length", default="")
    n.add_argument("--style", default="")
    n.add_argument("--avoid", default="")
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)
    b = sub.add_parser("batch")
    b.add_argument("dir")
    b.add_argument("--topic", action="append", required=True)
    for opt, default in (("--platform", "微信公众号"), ("--audience", ""), ("--length", ""),
                         ("--style", ""), ("--avoid", "")):  # fmt: skip
        b.add_argument(opt, default=default)
    b.add_argument("--force", action="store_true")
    b.set_defaults(fn=cmd_batch)
    s = sub.add_parser("show")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)
    g = sub.add_parser("get")
    g.add_argument("path")
    g.add_argument("section")
    g.add_argument("-o", "--output")
    g.set_defaults(fn=cmd_get)
    p = sub.add_parser("put")
    p.add_argument("path")
    p.add_argument("section")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_put)
    h = sub.add_parser("hash")
    h.add_argument("path")
    h.add_argument("--section", default="初稿")
    h.set_defaults(fn=cmd_hash)
    v = sub.add_parser("validate")
    v.add_argument("path")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_validate)
    gt = sub.add_parser("gate")
    gt.add_argument("path")
    gt.add_argument("--for", dest="for_", required=True)
    gt.add_argument("--confirmed", action="store_true")
    gt.add_argument("--json", action="store_true")
    gt.set_defaults(fn=cmd_gate)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
