#!/usr/bin/env python3
"""配图单：给文章包登记配图（搜索来的或生成的），检查图片和授权，列出要放进共享文件夹的文件。

配图不改初稿：图片按“位置”记在文章包的“配图单”里，预览和发布时再插进正文，所以核验报告不会因为配图而过期。

Usage:
    python scripts/figures.py plan 文章包.md                       列出正文段落编号和小标题，给出配图建议
    python scripts/figures.py add 文章包.md --file generated/images/x.jpg --pos 封面 --mode 生成 \\
        --caption 图注 --prompt 提示词 --model 模型                登记一张生成的图
    python scripts/figures.py fetch 文章包.md --url 图片地址 --page 来源页 --credit 署名 --license 授权 \\
        --pos 第3段后 --caption 图注                               下载并登记一张搜索来的图
    python scripts/figures.py remove 文章包.md P2
    python scripts/figures.py check 文章包.md [--json]              检查配图单、文件是否存在、哈希是否一致
    python scripts/figures.py share 文章包.md [--json]              列出要用 shared_file_put 放进共享文件夹的文件

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
    for fig in pack.figures():
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
    body = max(1, min(len(heads) or (len(paras) + 3) // 4, 5))
    print(
        f"建议：封面 1 张（公众号封面约 2.35:1）；正文 {body} 张，放在小标题后或段落之间；不要连续两段都配图"
    )
    if pack.figures():
        print("已登记：" + "，".join(f"{f.id}（{f.position}，{f.mode}）" for f in pack.figures()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="配图单")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("pack")
    pl.set_defaults(fn=cmd_plan)
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
