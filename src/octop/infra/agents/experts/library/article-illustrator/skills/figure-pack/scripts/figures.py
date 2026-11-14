#!/usr/bin/env python3
"""配图单：给文章包登记配图（搜索来的或生成的），检查图片和授权，列出要放进共享文件夹的文件。

配图不改初稿：图片按“位置”记在文章包的“配图单”里，预览和发布时再插进正文，所以核验报告不会因为配图而过期。

Usage:
    python scripts/figures.py plan 文章包.md                       列出正文段落编号和小标题，给出配图建议
    python scripts/figures.py style 文章包.md "扁平插画，蓝绿主色，柔和光线"   设定整篇统一的画面风格（不带文字只看）
    python scripts/figures.py context 文章包.md --pos 第3段后 [--window 2] [--json]
                                                                   取某个位置的上下文：读者和风格、标题、所在小节、
                                                                   前后段落、引用来源的摘录、已有配图，并给出提示词骨架
    python scripts/figures.py add 文章包.md --file generated/images/x.jpg --pos 封面 --mode 生成 \\
        --caption 图注 --prompt 提示词 --model 模型                登记一张生成的图
    python scripts/figures.py fetch 文章包.md --url 图片地址 --page 来源页 --credit 署名 --license 授权 \\
        --pos 第3段后 --caption 图注                               下载并登记一张搜索来的图
    python scripts/figures.py remove 文章包.md P2
    python scripts/figures.py check 文章包.md [--json]              检查配图单、文件是否存在、哈希是否一致
    python scripts/figures.py share 文章包.md [--json]              列出要用 shared_file_put 放进共享文件夹的文件

    python scripts/figures.py spots 文章包.md [--count 3] [--json]   给每段打分，推荐配图位置（点明中心事项 / 帮读者看懂），附关键句
    python scripts/figures.py prompt 文章包.md --pos 「要等多久」后 --role 衔接 --point 画面要点 \
        --elements "元素1,元素2,元素3" [--scene 场景氛围]          按上下文拼出提示词，并检查元素都来自文章

每张图都要说清“为什么放在这里”（add 时必填，check 会逐条核对）：
    用途：点题（封面 / 开头表现全文主旨；正文里点明这一节的中心事项）| 衔接（承上启下，画面同时有上文和下文的元素）
          | 说明（帮读者看懂刚读完的那段：流程、对比、数字、结构）
    位置要紧跟内容：点题和说明图放在依据所在段落的后面（第N段后，依据在第N段或第N-1段），
          或放在小标题后（依据在这一节头两段）；check 会拦住“图离它讲的内容太远”和“相邻两段都配图”
    画面要点：一句话，这张图让读者看懂什么          依据：从初稿逐字摘的原句，用「」括起，可多句
    画面元素：画面里的具体人、物、场景，逗号分隔，每个都必须是文章或来源摘录里出现过的说法
    画面描述：生成或找到图之后，看图写下画面里实际有什么（要能看到大部分画面元素）

登记时图片复制到 images/<话题>/P<n>.<扩展名>，共享名是 <话题>/P<n>.<扩展名>。
add / fetch 加 --crop 2.35:1 可按比例居中裁剪（公众号封面常用 2.35:1，需要 Pillow），裁剪后存为 JPG。
Exit codes: 0 正常, 3 check 未通过, 1 输入错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import article_pack as ap  # noqa: E402

MAX_BYTES = 15 * 1024 * 1024
MIN_BYTES = 1024
SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def image_ext(data: bytes) -> str | None:
    for sig, ext in SIGNATURES:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def fields_block(fig_id: str, fields: dict[str, str]) -> str:
    return "\n".join([f"### {fig_id}", *(f"- {k}：{v}" for k, v in fields.items() if v)])


def write_figures(path: Path, pack: ap.Pack, figures: list[tuple[str, dict[str, str]]]) -> None:
    if figures:
        pack.sections["配图单"] = "\n\n".join(fields_block(i, f) for i, f in figures)
    else:
        pack.sections.pop("配图单", None)
    ap.write_pack(path, pack)


def current(pack: ap.Pack) -> list[tuple[str, dict[str, str]]]:
    return [(f.id, dict(f.fields)) for f in pack.figures()]


def next_id(figures: list[tuple[str, dict[str, str]]]) -> str:
    nums = [int(i[1:]) for i, _ in figures if i[1:].isdigit()]
    return f"P{max(nums, default=0) + 1}"


def read_image(data: bytes, origin: str) -> str:
    if len(data) > MAX_BYTES:
        raise SystemExit(f"{origin} 超过 {MAX_BYTES // 1024 // 1024} MB")
    if len(data) < MIN_BYTES:
        raise SystemExit(f"{origin} 只有 {len(data)} 字节，不像一张正常的图片")
    ext = image_ext(data)
    if ext is None:
        raise SystemExit(f"{origin} 不是 JPG、PNG、GIF 或 WebP 图片")
    return ext


def crop(data: bytes, ratio: str) -> bytes:
    """Center-crop *data* to ``W:H`` and return JPEG bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:：xX/]\s*(\d+(?:\.\d+)?)\s*", ratio)
    if not m or float(m.group(2)) == 0:
        raise SystemExit(f"--crop 应写成 宽:高，如 2.35:1，而不是“{ratio}”")
    target = float(m.group(1)) / float(m.group(2))
    try:
        from io import BytesIO  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415
    except ImportError:
        raise SystemExit(
            "裁剪需要 Pillow（pip install pillow）；也可以生成时直接要对应比例"
        ) from None
    with Image.open(BytesIO(data)) as im:
        w, h = im.size
        if w / h > target:
            nw = round(h * target)
            box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
        else:
            nh = round(w / target)
            box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
        out = BytesIO()
        im.crop(box).convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def register(a: argparse.Namespace, data: bytes, origin: str, extra: dict[str, str]) -> int:
    path = Path(a.pack)
    pack = ap.read_pack(path)
    if not pack.front:
        print("目标文件不是文章包（缺少文件头）", file=sys.stderr)
        return 1
    ext = read_image(data, origin)
    if a.crop:
        data = crop(data, a.crop)
        ext = ".jpg"
    problem = ap.position_error(a.pos, pack.draft())
    if problem:
        print(problem, file=sys.stderr)
        return 1
    if not a.caption.strip():
        print("图注不能为空：写清画面内容，不要写初稿里没有的事实", file=sys.stderr)
        return 1
    grounding = {
        "用途": a.role.strip(),
        "画面要点": a.point.strip(),
        "依据": a.basis.strip(),
        "画面元素": "，".join(split_elements(a.elements)),
        "画面描述": a.seen.strip(),
    }
    errors = [
        m for lvl, m in grounding_problems(pack, a.pos, grounding, a.caption) if lvl == "error"
    ]
    if errors:
        print("配图和上下文对不上，先改好再登记：", file=sys.stderr)
        for m in errors:
            print(f"- {m}", file=sys.stderr)
        return 1
    figures = current(pack)
    fig_id = a.id or next_id(figures)
    if not re.fullmatch(r"P\d+", fig_id):
        print("编号应为 P1、P2 …", file=sys.stderr)
        return 1
    if a.pos == "封面" and any(f.get("位置") == "封面" and i != fig_id for i, f in figures):
        print("已经有封面了：先 remove 旧封面，或把这张放到正文位置", file=sys.stderr)
        return 1
    slug = ap.slugify(pack.topic or path.stem)
    rel = Path(a.root) / slug / f"{fig_id}{ext}"
    rel.parent.mkdir(parents=True, exist_ok=True)
    rel.write_bytes(data)
    fields = {
        "位置": a.pos,
        "方式": a.mode,
        "文件": rel.as_posix(),
        "共享名": f"{slug}/{fig_id}{ext}",
        "哈希": ap.file_hash(data),
        "图注": a.caption.strip(),
        **grounding,
        **extra,
    }
    replaced = any(i == fig_id for i, _ in figures)
    figures = [(i, fields if i == fig_id else f) for i, f in figures]
    if not replaced:
        figures.append((fig_id, fields))
    write_figures(path, pack, figures)
    print(
        f"{'已替换' if replaced else '已登记'} {fig_id}：{a.pos}，{a.mode}，{rel.as_posix()}（{len(data)} 字节）"
    )
    print(
        f"共享名 {fields['共享名']}：配图完成后用 shared_file_put 放进共享文件夹（figures.py share 会列出全部）"
    )
    return 0


def cmd_add(a: argparse.Namespace) -> int:
    src = Path(a.file)
    if not src.is_file():
        print(f"找不到图片：{src}", file=sys.stderr)
        return 1
    if a.mode == "生成":
        if not (a.prompt and a.model):
            print("生成的图要写 --prompt 和 --model", file=sys.stderr)
            return 1
        extra = {"提示词": a.prompt.strip(), "模型": a.model.strip()}
    else:
        missing = [k for k, v in (("--url", a.url), ("--page", a.page), ("--credit", a.credit),
                                  ("--license", a.license)) if not v]  # fmt: skip
        if missing:
            print(f"搜索来的图要写 {'、'.join(missing)}", file=sys.stderr)
            return 1
        extra = {"网址": a.url, "来源页": a.page, "署名": a.credit, "授权": a.license}
    return register(a, src.read_bytes(), str(src), extra)


def download(url: str) -> bytes:
    if not re.match(r"^https?://", url):
        raise SystemExit("图片地址必须以 http:// 或 https:// 开头")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 OctopIllustrator"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"下载失败 {url}：{getattr(exc, 'reason', exc)}") from None
    return data


def cmd_fetch(a: argparse.Namespace) -> int:
    missing = [
        k
        for k, v in (("--page", a.page), ("--credit", a.credit), ("--license", a.license))
        if not v
    ]
    if missing:
        print(f"搜索来的图要写 {'、'.join(missing)}", file=sys.stderr)
        return 1
    if a.license.strip() in ap.UNKNOWN_LICENSE:
        print("授权不明的图不能用：换一张能确认授权的图，或改用生图", file=sys.stderr)
        return 1
    data = download(a.url)
    extra = {"网址": a.url, "来源页": a.page, "署名": a.credit, "授权": a.license}
    a.mode = "搜索"
    return register(a, data, a.url, extra)


def cmd_remove(a: argparse.Namespace) -> int:
    path = Path(a.pack)
    pack = ap.read_pack(path)
    figures = current(pack)
    keep = [(i, f) for i, f in figures if i != a.id]
    if len(keep) == len(figures):
        print(f"配图单里没有 {a.id}", file=sys.stderr)
        return 1
    write_figures(path, pack, keep)
    print(f"已移除 {a.id}（图片文件保留，不需要可以自己删）")
    return 0


def check(pack: ap.Pack, base: Path) -> list[ap.Issue]:
    issues = [i for i in ap.validate(pack) if i.where == "配图单" or re.fullmatch(r"P\d+", i.where)]
    if "配图单" not in pack.sections:
        issues.append(ap.Issue("error", "配图单", "文章包里还没有配图单"))
        return issues
    for fid, lvl, msg in spacing_problems(pack):
        issues.append(ap.Issue(lvl, fid, msg))
    for fig in pack.figures():
        for lvl, msg in grounding_problems(
            pack, fig.position, fig.fields, fig.fields.get("图注", "")
        ):
            issues.append(ap.Issue(lvl, fig.id, msg))
        rel = fig.fields.get("文件", "")
        if not rel:
            continue
        p = base / rel
        if not p.is_file():
            issues.append(ap.Issue("error", fig.id, f"找不到图片文件 {rel}"))
            continue
        data = p.read_bytes()
        if fig.fields.get("哈希") and ap.file_hash(data) != fig.fields["哈希"]:
            issues.append(
                ap.Issue("error", fig.id, f"{rel} 和登记时不一样了（哈希不符），请重新 add")
            )
        if image_ext(data) is None:
            issues.append(ap.Issue("error", fig.id, f"{rel} 不是支持的图片格式"))
    return issues


def cmd_check(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    issues = check(pack, Path(a.base))
    errors = [i for i in issues if i.level == "error"]
    if a.json:
        print(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=2))
    else:
        for i in issues:
            print(f"[{'错误' if i.level == 'error' else '提示'}] {i.where}：{i.message}")
        n = len(pack.figures())
        print(
            f"配图 {n} 张，{len(errors)} 个错误，{len(issues) - len(errors)} 个提示"
            if issues
            else f"配图 {n} 张，没有问题"
        )
    return 3 if errors else 0


def cmd_share(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    rows = [
        {"编号": f.id, "文件": f.fields.get("文件", ""), "共享名": f.fields.get("共享名", "")}
        for f in pack.figures()
    ]
    if not rows:
        print("配图单是空的", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("逐个调用 shared_file_put(path=文件, name=共享名, overwrite=true)：")
    for r in rows:
        print(f"- {r['编号']}：{r['文件']} → {r['共享名']}")
    return 0


PARA_LIMIT = 400
EXCERPT_LIMIT = 160
MARK_RE = re.compile(r"\[(?:S\d+(?:\s*[,，、]\s*S\d+)*|推算)\]")


def clean(text: str) -> str:
    return re.sub(r"\s+([。！？，；：、）])", r"\1", MARK_RE.sub("", text)).strip()


def outline(draft: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(title, digest, [(section heading, paragraph)]) with paragraphs numbered as in positions."""
    title, digest, rows = "", "", []
    heading = ""
    for line in draft.splitlines():
        s = line.strip()
        if not s:
            continue
        if not title and re.match(r"^#\s+\S", s):
            title = s.lstrip("# ").strip()
            continue
        if ap.REF_HEAD_RE.match(s):
            break
        m = re.match(r"^(?:摘要|导语)\s*[：:]\s*(.*)$", s)
        if m:
            digest = digest or m.group(1).strip()
            continue
        h = re.match(r"^#{2,6}\s+(.+?)\s*$", s)
        if h:
            heading = h.group(1).strip()
            continue
        if ap.SKIP_PARA_RE.match(s):
            continue
        rows.append((heading, s))
    return title, digest, rows


def brief_fields(pack: ap.Pack) -> dict[str, str]:
    return ap.parse_fields(pack.sections.get("选题单", "").splitlines())


def slot_context(pack: ap.Pack, pos: str, window: int) -> dict[str, Any]:
    """Everything an image prompt for *pos* should draw on."""
    draft = pack.draft()
    title, digest, rows = outline(draft)
    if pos == "封面":
        picked = list(range(min(len(rows), 2 + window)))
        section = ""
    elif pos == "开头":
        picked = list(range(min(len(rows), 1 + window)))
        section = rows[0][0] if rows else ""
    elif m := ap.POS_PARA_RE.match(pos):
        n = int(m.group(1)) - 1
        picked = [i for i in range(n - window, n + window + 1) if 0 <= i < len(rows)]
        section = rows[n][0] if 0 <= n < len(rows) else ""
    elif m := ap.POS_HEAD_RE.match(pos):
        section = m.group(1).strip()
        picked = [i for i, (h, _) in enumerate(rows) if h == section]
        if picked:
            after = [i for i in range(picked[-1] + 1, picked[-1] + 1 + window) if i < len(rows)]
            picked = picked[: window * 2 + 2] + after[:1]
    else:
        raise SystemExit(f"位置“{pos}”应为 封面、开头、第N段后 或 「小标题」后")
    anchor = None
    if m := ap.POS_PARA_RE.match(pos):
        anchor = int(m.group(1))
    paras = [
        {
            "段": i + 1,
            "小节": rows[i][0],
            "锚点": anchor == i + 1,
            "原文": clean(rows[i][1])[:PARA_LIMIT],
        }
        for i in picked
    ]
    cited = ap.citation_ids("\n".join(rows[i][1] for i in picked))
    by_id = {src.id: src for src in pack.sources()}
    excerpts = [
        {
            "来源": sid,
            "标题": by_id[sid].fields.get("标题", ""),
            "摘录": by_id[sid].excerpt.replace("\n", " ")[:EXCERPT_LIMIT],
        }
        for sid in cited
        if sid in by_id and by_id[sid].excerpt
    ]
    brief = brief_fields(pack)
    before, after, before_name, after_name = sides(pack, pos)
    return {
        "位置": pos,
        "建议用途": suggest_role(pack, pos),
        "上文": {"范围": before_name, "原文": clean(before)[: PARA_LIMIT * 2]},
        "下文": {"范围": after_name, "原文": clean(after)[: PARA_LIMIT * 2]},
        "话题": pack.topic,
        "读者": brief.get("读者", ""),
        "风格要求": brief.get("风格", ""),
        "统一画面风格": pack.front.get("figure-style", ""),
        "标题": clean(title),
        "摘要": clean(digest),
        "全文小标题": [h for h in ap.headings(draft) if not ap.REF_HEAD_RE.match(f"## {h}")],
        "所在小节": section,
        "上下文段落": paras,
        "相关来源摘录": excerpts,
        "已有配图": [
            {
                "编号": f.id,
                "位置": f.position,
                "方式": f.mode,
                "图注": f.fields.get("图注", ""),
                "提示词": f.fields.get("提示词", ""),
            }  # fmt: skip
            for f in pack.figures()
        ],
    }


def suggest_role(pack: ap.Pack, pos: str) -> str:
    if pos in ("封面", "开头"):
        return "点题"
    _title, _digest, rows = outline(pack.draft())
    heads = [h for h, _ in rows]
    if m := ap.POS_HEAD_RE.match(pos):
        return "衔接" if heads and heads[0] != m.group(1).strip() else "说明"
    if m := ap.POS_PARA_RE.match(pos):
        n = int(m.group(1))
        if n < len(rows) and heads[n - 1] != heads[n]:
            return "衔接"
    return "说明"


def prompt_skeleton(ctx: dict[str, Any]) -> list[str]:
    cover = ctx["位置"] == "封面"
    style = ctx["统一画面风格"] or ctx["风格要求"] or "（先用 style 定下整篇统一的风格）"
    role = ctx.get("建议用途", "说明")
    duty = {
        "点题": "点题：一张图说出全文主旨（看标题、摘要和开头），读者只看封面就知道这篇讲什么",
        "衔接": f"衔接：承上启下，画面里既有上文（{ctx['上文']['范围']}）的东西，也有下文（{ctx['下文']['范围']}）的东西，"
        "用前后景、左右构图或光线变化表现两者的联系",
        "说明": "说明：把锚点段落讲的那件事画出来，读者看图就懂这一段",
    }[role]
    return [
        f"0. 用途（建议）：{duty}",
        "1. 这张图要让读者一眼明白什么（一句话，必须来自上面的段落，不要泛泛的“科技感”“未来感”）：",
        "2. 画面里的具体东西（2–4 个，用段落里出现的具体名词：地点、物件、人物身份、动作、时间）：",
        "3. 场景和氛围（时间、天气、光线、情绪，要和段落的语气一致）：",
        f"4. 统一风格：{style}",
        "5. 构图："
        + (
            "横版宽幅 2.35:1，主体居中，左右留白，缩成 1:1 时主体仍完整"
            if cover
            else "横版 16:9，主体清晰，背景简洁"
        ),  # fmt: skip
        "6. 画面中不出现任何文字、数字、标志和水印；不出现真实人物的脸和商标",
        "参考图：正文配图把已生成的封面作为 reference_images 传给 generate_image，保持人物、配色和画风一致",
        "填好后用 figures.py prompt --role … --point … --elements … 拼出提示词（会检查元素是否都来自文章）",
    ]


def cmd_context(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    if not pack.draft():
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    problem = ap.position_error(a.pos, pack.draft())
    if problem:
        print(problem, file=sys.stderr)
        return 1
    ctx = slot_context(pack, a.pos, max(0, a.window))
    if a.json:
        print(json.dumps({**ctx, "提示词骨架": prompt_skeleton(ctx)}, ensure_ascii=False, indent=2))
        return 0
    print(
        f"【位置】{a.pos}　【建议用途】{ctx['建议用途']}　【话题】{ctx['话题']}　【读者】{ctx['读者'] or '-'}"
    )
    print(f"【标题】{ctx['标题']}")
    if ctx["摘要"]:
        print(f"【摘要】{ctx['摘要']}")
    print(
        f"【风格】选题单：{ctx['风格要求'] or '-'}；统一画面风格：{ctx['统一画面风格'] or '未设定'}"
    )
    print(
        "【全文结构】" + " → ".join(ctx["全文小标题"])
        if ctx["全文小标题"]
        else "【全文结构】无小标题"
    )
    if ctx["所在小节"]:
        print(f"【所在小节】「{ctx['所在小节']}」")
    print("【上下文段落】")
    for p in ctx["上下文段落"]:
        mark = "  ◀ 图放在这一段后面" if p["锚点"] else ""
        print(f"  第{p['段']}段{('（' + p['小节'] + '）') if p['小节'] else ''}：{p['原文']}{mark}")
    if ctx["建议用途"] == "衔接":
        print(f"【上文】{ctx['上文']['范围']}：{ctx['上文']['原文'][:200]}")
        print(f"【下文】{ctx['下文']['范围']}：{ctx['下文']['原文'][:200]}")
    if ctx["相关来源摘录"]:
        print("【相关来源摘录】（具体的地点、物件、数字背景可以从这里取，但画面里不写数字）")
        for e in ctx["相关来源摘录"]:
            print(f"  {e['来源']}《{e['标题']}》：{e['摘录']}")
    if ctx["已有配图"]:
        print("【已有配图】（不要重复同一个画面，风格保持一致）")
        for f in ctx["已有配图"]:
            print(f"  {f['编号']}（{f['位置']}，{f['方式']}）{f['图注']}")
    print("【提示词骨架】逐条填好，合成一段提示词再调用 generate_image：")
    for line in prompt_skeleton(ctx):
        print(f"  {line}")
    return 0


def cmd_style(a: argparse.Namespace) -> int:
    path = Path(a.pack)
    pack = ap.read_pack(path)
    if not pack.front:
        print("目标文件不是文章包（缺少文件头）", file=sys.stderr)
        return 1
    if a.text is None:
        print(pack.front.get("figure-style") or "还没有设定统一画面风格")
        return 0
    text = " ".join(a.text.split())
    if not text:
        pack.front.pop("figure-style", None)
    else:
        pack.front["figure-style"] = text
    ap.write_pack(path, pack)
    print(f"统一画面风格：{text or '已清除'}（每张生成图的提示词都要带上它）")
    return 0


ROLES = ("点题", "衔接", "说明")
QUOTE_RE = re.compile(r"「([^」]+)」")


def flat(text: str) -> str:
    """Draft text without citation marks and whitespace, for verbatim quote matching."""
    return re.sub(r"\s+", "", MARK_RE.sub("", text))


def split_elements(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，、;；]", raw or "") if x.strip()]


def quotes(basis: str) -> list[str]:
    found = QUOTE_RE.findall(basis or "")
    return [q.strip() for q in (found or [basis or ""]) if q.strip()]


def sides(pack: ap.Pack, pos: str) -> tuple[str, str, str, str]:
    """(上文, 下文, 上文名, 下文名) around *pos*, used for 衔接 and 说明 checks."""
    title, digest, rows = outline(pack.draft())
    heads = [h for h, _ in rows]
    if pos in ("封面", "开头"):
        head = "\n".join([title, digest, *(t for _, t in rows[:2])])
        return head, "\n".join(t for _, t in rows[2:4]), "标题和开头", "正文"
    if m := ap.POS_HEAD_RE.match(pos):
        h = m.group(1).strip()
        idx = [i for i, x in enumerate(heads) if x == h]
        if not idx:
            return "", "", "", ""
        first = idx[0]
        prev_head = heads[first - 1] if first else ""
        prev = [t for x, t in rows[:first] if x == prev_head][-3:]
        nxt = [rows[i][1] for i in idx][:3]
        return "\n".join(prev), "\n".join(nxt), f"「{prev_head or '开头'}」", f"「{h}」"
    if m := ap.POS_PARA_RE.match(pos):
        n = int(m.group(1)) - 1
        if not 0 <= n < len(rows):
            return "", "", "", ""
        here = heads[n]
        prev = [rows[i][1] for i in range(max(0, n - 2), n + 1)]
        nxt = [rows[i][1] for i in range(n + 1, min(len(rows), n + 3))]
        nxt_head = heads[n + 1] if n + 1 < len(rows) else ""
        return (
            "\n".join(prev),
            "\n".join(nxt),
            f"第{n + 1}段及之前" + (f"（「{here}」）" if here else ""),
            f"第{n + 2}段起" + (f"（「{nxt_head}」）" if nxt_head and nxt_head != here else ""),
        )
    return "", "", "", ""


def anchor_of(pack: ap.Pack, pos: str) -> int:
    """Paragraph number the figure follows (0 = before the first paragraph)."""
    _title, _digest, rows = outline(pack.draft())
    if m := ap.POS_PARA_RE.match(pos):
        return int(m.group(1))
    if m := ap.POS_HEAD_RE.match(pos):
        idx = [i for i, (h, _) in enumerate(rows) if h == m.group(1).strip()]
        return idx[0] if idx else 0
    return 0


def quote_paragraphs(pack: ap.Pack, quote: str) -> list[int]:
    """Paragraph numbers (1-based) containing *quote*; 0 for the title or digest."""
    title, digest, rows = outline(pack.draft())
    q = flat(quote)
    hits = [i + 1 for i, (_, t) in enumerate(rows) if q in flat(t)]
    if q in flat(title) or q in flat(digest):
        hits.insert(0, 0)
    return hits


def placement_problem(pack: ap.Pack, pos: str, good: list[str]) -> str | None:
    """A 点题 / 说明 figure must sit right where its quoted text is read.

    After paragraph N: the quote is in paragraph N or N-1 (the reader has just read it).
    After a heading: the quote is in the first two paragraphs of that section (the figure leads it).
    """
    anchor = anchor_of(pack, pos)
    found = sorted({n for q in good for n in quote_paragraphs(pack, q) if n})
    if not found:
        return None
    if ap.POS_HEAD_RE.match(pos):
        ok = {anchor + 1, anchor + 2}
        where = f"{pos.removesuffix('后')}这一节的头两段"
    else:
        ok = {anchor, anchor - 1}
        where = f"第{anchor}段或第{anchor - 1}段"
    if ok & set(found):
        return None
    best = found[0]
    return (
        f"图放的位置离它要讲的内容太远：依据在第{'、'.join(map(str, found))}段，图在{pos}，"
        f"依据应在{where}。建议改放到“第{best}段后”，或换成这里附近的原句"
    )


CENTER_CUES = (
    "关键",
    "核心",
    "最重要",
    "重点",
    "意味着",
    "总之",
    "说白了",
    "一句话",
    "结论",
    "本质",
    "真正",
)
EXPLAIN_CUES = ("首先", "其次", "然后", "最后", "流程", "步骤", "环节", "先", "再",
                "相比", "对比", "区别", "不同", "一方面", "另一方面", "而是", "位于", "分布", "结构", "组成", "包括")  # fmt: skip


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {t[i : i + 2] for i in range(len(t) - 1)}


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[。！？；])", text) if x.strip()]


def score_spots(pack: ap.Pack) -> list[dict[str, Any]]:
    """Rank every body paragraph as a place for a figure, with the reason and the key sentence."""
    title, digest, rows = outline(pack.draft())
    theme = bigrams(title + digest + pack.topic)
    out: list[dict[str, Any]] = []
    for i, (head, raw) in enumerate(rows):
        text = clean(raw)
        n = i + 1
        sents = sentences(text) or [text]
        local = theme | bigrams(head)

        def center(
            sent: str, local: set[str] = local, cited: bool = bool(ap.CITE_RE.search(raw))
        ) -> float:
            b = bigrams(sent)
            overlap = len(b & local) / (len(b) ** 0.5 or 1)
            return overlap + 1.5 * sum(c in sent for c in CENTER_CUES) + 0.5 * cited

        def explain(sent: str) -> float:
            nums = len(re.findall(r"\d+(?:\.\d+)?", sent))
            return (
                1.0 * sum(c in sent for c in EXPLAIN_CUES) + 0.5 * min(nums, 4) + (len(sent) > 60)
            )

        key_c = max(sents, key=center)
        key_e = max(sents, key=explain)
        c_score, e_score = center(key_c), explain(key_e) + (len(text) > 140)
        boundary = n < len(rows) and rows[n][0] != head
        if c_score >= e_score:
            role, why, key, score = "点题", "点明中心事项", key_c, c_score
        else:
            role, why, key, score = (
                "说明",
                "帮读者看懂这段（流程、对比、数字或结构）",
                key_e,
                e_score,
            )
        if len(text) < 25:
            score -= 2
        out.append({"段": n, "小节": head, "建议用途": role, "理由": why, "关键句": key.strip(),
                    "得分": round(score, 2), "节末": boundary})  # fmt: skip
    return out


def pick_spots(spots: list[dict[str, Any]], count: int, taken: set[int]) -> list[dict[str, Any]]:
    """Best *count* spots, at least two paragraphs apart from each other and from *taken*."""
    chosen: list[dict[str, Any]] = []
    for spot in sorted(spots, key=lambda x: -x["得分"]):
        if len(chosen) >= count:
            break
        near = taken | {c["段"] for c in chosen}
        if spot["段"] == 1 or any(abs(spot["段"] - t) < 2 for t in near):
            continue
        chosen.append(spot)
    return sorted(chosen, key=lambda x: x["段"])


def spacing_problems(pack: ap.Pack) -> list[tuple[str, str, str]]:
    """(figure id, level, message) for body figures that crowd each other."""
    body = [(anchor_of(pack, f.position), f) for f in pack.figures() if f.position not in ("封面",)]
    body.sort(key=lambda x: x[0])
    out = []
    for (a1, f1), (a2, f2) in zip(body, body[1:], strict=False):
        if a1 == a2:
            out.append((f2.id, "error", f"{f2.id} 和 {f1.id} 放在同一个位置：一个位置只放一张图"))
        elif a2 - a1 < 2:
            out.append(
                (f2.id, "warn", f"{f2.id} 和 {f1.id} 只隔 {a2 - a1} 段：相邻两段都配图会打断阅读")
            )
    return out


def cmd_spots(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    if not pack.draft():
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    spots = score_spots(pack)
    taken = {anchor_of(pack, f.position) for f in pack.figures() if f.position != "封面"}
    count = a.count or max(1, min(4, (len(spots) + 2) // 3))
    chosen = pick_spots(spots, count, taken)
    if a.json:
        print(json.dumps({"推荐": chosen, "全部": spots}, ensure_ascii=False, indent=2))
        return 0
    print(
        f"正文 {len(spots)} 段，推荐 {len(chosen)} 个配图位置（相互至少隔一段，已有配图的位置附近不再推荐）："
    )
    for s in chosen:
        print(f"- 第{s['段']}段后（{s['小节'] or '开头部分'}）：{s['建议用途']}，{s['理由']}")
        print(f"    关键句：{s['关键句']}")
        print(f"    依据可直接用：「{s['关键句'].rstrip('。！？；')}」")
    bounds = [s for s in spots if s["节末"]]
    if bounds:
        print(
            "两节之间（适合放衔接图）："
            + "，".join(
                f"第{s['段']}段后（{'「' + s['小节'] + '」' if s['小节'] else '开头部分'}结束）"
                for s in bounds
            )
        )
    print("用 context --pos 看清上下文后再定；图要紧跟在它讲的那句话后面，不要隔好几段")
    return 0


def grounding_problems(
    pack: ap.Pack, pos: str, f: dict[str, str], caption: str
) -> list[tuple[str, str]]:
    """Why a figure is not tied to its place in the article: (level, message) pairs."""
    out: list[tuple[str, str]] = []
    role, point = f.get("用途", ""), f.get("画面要点", "")
    elements = split_elements(f.get("画面元素", ""))
    seen = f.get("画面描述", "")
    if role not in ROLES:
        out.append(("error", f"用途应为 {'、'.join(ROLES)}（写明这张图是点题、衔接还是说明）"))
    if not point:
        out.append(("error", "缺少画面要点：一句话写清这张图让读者看懂什么"))
    if not elements:
        out.append(("error", "缺少画面元素：列出画面里的具体人、物、场景"))
    if not seen:
        out.append(("error", "缺少画面描述：看图后写下画面里实际有什么"))
    if role == "衔接" and pos in ("封面", "开头"):
        out.append(("error", "封面和开头没有上文，不能做衔接图"))
    draft = flat(pack.draft())
    pool = draft + flat("\n".join(s.excerpt for s in pack.sources()))
    for e in elements:
        if flat(e) not in pool:
            out.append(("error", f"画面元素“{e}”在初稿和来源摘录里找不到：换成文中出现过的说法"))
    qs = quotes(f.get("依据", ""))
    if not qs:
        out.append(("error", "缺少依据：从初稿逐字摘一两句原文，用「」括起"))
    bad = [q for q in qs if flat(q) not in draft]
    for q in bad:
        out.append(("error", f"依据「{q}」不是初稿里的原文（要逐字摘，可以只摘半句）"))
    if len(flat("".join(qs))) and all(len(flat(q)) < 6 for q in qs):
        out.append(("warn", "依据太短：至少摘一句能说明画面的话"))
    before, after, before_name, after_name = sides(pack, pos)
    good = [q for q in qs if q not in bad]
    if role == "衔接" and good:
        if not any(flat(q) in flat(before) for q in good):
            out.append(("error", f"衔接图的依据里没有上文（{before_name}）的原句"))
        if not any(flat(q) in flat(after) for q in good):
            out.append(("error", f"衔接图的依据里没有下文（{after_name}）的原句"))
    if role == "衔接" and elements and before and after:
        if not any(flat(e) in flat(before) for e in elements):
            out.append(
                (
                    "error",
                    f"衔接图的画面元素里没有上文（{before_name}）出现的东西：至少放一个上文里的人、物或场景",
                )
            )
        if not any(flat(e) in flat(after) for e in elements):
            out.append(
                (
                    "error",
                    f"衔接图的画面元素里没有下文（{after_name}）出现的东西：至少放一个下文里的人、物或场景",
                )
            )
    if role in ("点题", "说明") and good and pos not in ("封面", "开头"):
        problem = placement_problem(pack, pos, good)
        if problem:
            out.append(("error", problem))
    if role == "点题" and good and pos in ("封面", "开头"):
        title, digest, rows = outline(pack.draft())
        lead = flat(
            "\n".join([title, digest, *(t for _, t in rows[:2]), *(t for _, t in rows[-1:])])
        )
        if not any(flat(q) in lead for q in good):
            out.append(("warn", "封面和开头的点题图，依据应来自标题、摘要、开头或结尾（全文主旨）"))
    if seen and elements:
        hit = [e for e in elements if flat(e) in flat(seen)]
        if len(hit) * 2 < len(elements):
            missing = "、".join(e for e in elements if e not in hit)
            out.append(
                ("error", f"画面描述里没看到：{missing}。图和计划对不上，改提示词重画或改画面元素")
            )
    if caption and elements and not any(flat(e) in flat(caption) for e in elements):
        out.append(("warn", "图注里没有任何画面元素：图注要说清这张图和这一段的关系"))
    return out


def compose_prompt(
    pack: ap.Pack, pos: str, role: str, point: str, elements: list[str], scene: str
) -> str:
    style = pack.front.get("figure-style") or brief_fields(pack).get("风格", "")
    before, after, before_name, after_name = sides(pack, pos)
    els = "、".join(elements)
    if role == "点题":
        lead = f"一张表现文章主旨的{'公众号封面' if pos == '封面' else '题图'}：{point}。画面主体：{els}。"
    elif role == "衔接":
        prev = [e for e in elements if flat(e) in flat(before)]
        nxt = [e for e in elements if flat(e) in flat(after) and e not in prev]
        lead = (
            f"一张承上启下的配图，从{before_name}过渡到{after_name}：{point}。"
            f"前景或画面一侧是{'、'.join(prev) or els}，另一侧或背景是{'、'.join(nxt) or els}，"
            "用前后景、左右构图或光线变化表现两者的联系。"
        )
    else:
        lead = f"一张解释正文的配图：{point}。画面里要清楚看到：{els}。"
    comp = (
        "横版宽幅 2.35:1 构图，主体居中，左右留白，缩成 1:1 时主体仍完整"
        if pos == "封面"
        else "横版 16:9 构图，主体清晰，背景简洁"
    )
    parts = [lead, scene.strip().rstrip("。") + "。" if scene.strip() else "", f"{style}。" if style else "", f"{comp}。",
             "画面中不出现任何文字、数字、标志和水印，不出现真实人物的脸和商标。"]  # fmt: skip
    return "".join(x for x in parts if x)


def cmd_prompt(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    problem = ap.position_error(a.pos, pack.draft())
    if problem:
        print(problem, file=sys.stderr)
        return 1
    elements = split_elements(a.elements)
    fields = {
        "用途": a.role,
        "画面要点": a.point,
        "画面元素": "，".join(elements),
        "依据": a.basis or "",
        "画面描述": "-",
    }
    problems = [
        (lvl, m)
        for lvl, m in grounding_problems(pack, a.pos, fields, "")
        if "画面描述" not in m and ("依据" not in m or a.basis)
    ]
    for lvl, m in problems:
        print(f"[{'错误' if lvl == 'error' else '提示'}] {m}", file=sys.stderr)
    if any(lvl == "error" for lvl, _ in problems):
        return 1
    text = compose_prompt(pack, a.pos, a.role, a.point.strip(), elements, a.scene)
    if a.json:
        print(
            json.dumps(
                {"提示词": text, "用途": a.role, "画面元素": elements}, ensure_ascii=False, indent=2
            )
        )
        return 0
    print(text)
    print(
        "\n下一步：用上面的提示词调用 generate_image；看图后写画面描述，再 add："
        f'\n  figures.py add 文章包.md --file <图片> --mode 生成 --pos "{a.pos}" --role {a.role} '
        f'--point "{a.point.strip()}" --elements "{"，".join(elements)}" --basis "「…」「…」" '
        '--seen "<看图写下画面里实际有什么>" --prompt "<上面的提示词>" --model <模型> --caption <图注>'
    )
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    draft = pack.draft()
    if not draft:
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    paras = ap.paragraphs(draft)
    heads = [h for h in ap.headings(draft) if not ap.REF_HEAD_RE.match(f"## {h}")]
    print(f"话题：{pack.topic}；正文 {len(paras)} 段，小标题 {len(heads)} 个")
    for n, text in enumerate(paras, 1):
        print(f"  第{n}段：{text[:40]}{'…' if len(text) > 40 else ''}")
    for h in heads:
        print(f"  小标题：「{h}」")
    print("建议方案：")
    print("  封面：点题，表现全文主旨（依据取标题、摘要或开头）")
    chosen = pick_spots(score_spots(pack), max(1, min(4, (len(paras) + 2) // 3)), set())
    for spot in chosen:
        print(
            f"  第{spot['段']}段后：{spot['建议用途']}，{spot['理由']}；关键句：{spot['关键句'][:40]}"
        )
    print(
        "  两节之间转折明显、读者容易断的地方，可以把其中一张换成衔接图（spots 会列出各节结束的位置）"
    )
    print("  图要紧跟在它讲的那句话后面；相邻两段不要都配图")
    if pack.figures():
        print("已登记：" + "，".join(f"{f.id}（{f.position}，{f.mode}）" for f in pack.figures()))
    if not pack.front.get("figure-style"):
        print(
            "还没有统一画面风格：先读完全文，用 style 定下一句风格描述，再逐个位置用 context 取上下文写提示词"
        )
    else:
        print(
            f"统一画面风格：{pack.front['figure-style']}；每个位置先用 context 取上下文再写提示词"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="配图单")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("pack")
    pl.set_defaults(fn=cmd_plan)
    st = sub.add_parser("style")
    st.add_argument("pack")
    st.add_argument("text", nargs="?")
    st.set_defaults(fn=cmd_style)
    cx = sub.add_parser("context")
    cx.add_argument("pack")
    cx.add_argument("--pos", required=True)
    cx.add_argument("--window", type=int, default=2, help="锚点前后各取几段（默认 2）")
    cx.add_argument("--json", action="store_true")
    cx.set_defaults(fn=cmd_context)
    sp = sub.add_parser("spots")
    sp.add_argument("pack")
    sp.add_argument("--count", type=int, default=0, help="推荐几个位置（默认按篇幅 1–4 个）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_spots)
    pr = sub.add_parser("prompt")
    pr.add_argument("pack")
    pr.add_argument("--pos", required=True)
    pr.add_argument("--role", required=True, choices=list(ROLES))
    pr.add_argument("--point", required=True)
    pr.add_argument("--elements", required=True)
    pr.add_argument("--basis", default="")
    pr.add_argument("--scene", default="")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(fn=cmd_prompt)
    for name, fn in (("add", cmd_add), ("fetch", cmd_fetch)):
        s = sub.add_parser(name)
        s.add_argument("pack")
        if name == "add":
            s.add_argument("--file", required=True)
            s.add_argument("--mode", required=True, choices=list(ap.FIGURE_MODES))
            s.add_argument("--url", default="")
        else:
            s.add_argument("--url", required=True)
        s.add_argument("--pos", required=True)
        s.add_argument("--caption", required=True)
        s.add_argument("--id", default="")
        s.add_argument("--prompt", default="")
        s.add_argument("--model", default="")
        s.add_argument("--page", default="")
        s.add_argument("--credit", default="")
        s.add_argument("--license", default="")
        s.add_argument("--root", default="images", help="图片存放目录（相对当前目录）")
        s.add_argument("--crop", default="", help="按比例居中裁剪，如 2.35:1")
        s.add_argument("--role", required=True, choices=list(ROLES), help="点题 / 衔接 / 说明")
        s.add_argument("--point", required=True, help="画面要点：一句话")
        s.add_argument("--basis", required=True, help="依据：初稿原句，用「」括起")
        s.add_argument("--elements", required=True, help="画面元素，逗号分隔")
        s.add_argument("--seen", required=True, help="画面描述：看图后写下画面里实际有什么")
        s.set_defaults(fn=fn)
    r = sub.add_parser("remove")
    r.add_argument("pack")
    r.add_argument("id")
    r.set_defaults(fn=cmd_remove)
    c = sub.add_parser("check")
    c.add_argument("pack")
    c.add_argument("--base", default=".", help="“文件”路径相对的目录")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)
    sh = sub.add_parser("share")
    sh.add_argument("pack")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(fn=cmd_share)
    return p


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
