#!/usr/bin/env python3
"""把核验通过的文章包存进微信公众号草稿箱，并记录发布台账。只存草稿，不群发。

Usage:
    python scripts/wechat_draft.py check [--online]                         检查环境变量；--online 试取一次 access_token
    python scripts/wechat_draft.py render 文章包.md -o 文章.html [--theme green|blue|orange] [--author 作者]
    python scripts/wechat_draft.py figures 文章包.md [--figures-dir shared]      列出配图的共享名，检查是否已取回、哈希是否一致
    python scripts/wechat_draft.py draft 文章包.md --confirmed [--cover 封面.jpg] [--author 作者] [--theme green]
                                   [--digest 摘要] [--comment] [--dry-run] [--image-dir 图片目录] [--figures-dir shared]
                                   [--ledger outputs/publish-ledger.csv]
    python scripts/wechat_draft.py export 文章包.md --platform xiaohongshu|toutiao|plain -o 文本.txt
    python scripts/wechat_draft.py ledger list|check|add [--ledger 台账.csv] ...

环境变量（只从环境读取，脚本不打印密钥）：
    WECHAT_APP_ID / WECHAT_APP_SECRET   公众号开发者凭据
    WECHAT_API_BASE                     接口地址，默认 https://api.weixin.qq.com（测试时可指向本地假服务）

draft 的流程：
    1. 把关：文章包结构无错误 + 核验结论“通过” + 初稿自核验后没改过 + 用户已明确确认（--confirmed）；任何一项不满足就拒绝；
    2. 同一份初稿（哈希相同）已经存过草稿时拒绝重复，除非 --force；
    3. 渲染排版 HTML：去掉 [S1]、[推算] 标记，按“配图单”把配图插进正文（图片要先用 shared_file_get 取到
       --figures-dir，默认 shared/<共享名>，哈希必须和配图单一致），文末生成“参考来源”和“图片来源”；
       没给 --cover 时用配图单里位置为“封面”的图；
    4. 取 access_token → 上传封面 → 上传正文里的图片 → 新建草稿 → 写回文章包的“发布单”并记台账；
    --dry-run 只列出将要做的事，不联网、不写任何东西。
接口细节按微信开放文档编写，没有用真实公众号验证；权限不足时给出处理办法（见 references/wechat-api.md）。
Exit codes: 0 成功, 3 把关未通过或接口报错, 1 输入或环境错误。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import article_pack as ap  # noqa: E402

HERE = Path(__file__).resolve().parent
LAYOUT = HERE / "wechat_layout.py"
DEFAULT_BASE = "https://api.weixin.qq.com"
MARK_RE = re.compile(r"\[(?:S\d+(?:\s*[,，、]\s*S\d+)*|推算)\]")
LEDGER_COLS = ["时间", "话题", "平台", "状态", "media_id", "初稿哈希", "标题", "说明"]
ERRORS = {
    40164: "调用接口的 IP 不在公众号的 IP 白名单里：到公众号后台“设置与开发 → 基本配置”把本机出口 IP 加进白名单",
    40013: "AppID 不正确",
    40125: "AppSecret 不正确",
    40001: "access_token 无效，请检查 AppID 和 AppSecret",
    48001: "这个公众号没有该接口的权限（未认证的订阅号常见）。改用 render 导出 HTML，到后台编辑器手动粘贴",
    45009: "接口调用次数达到上限，稍后再试",
    40007: "media_id 无效",
    40005: "上传的文件类型不支持",
    40009: "图片尺寸或大小超限",
    44002: "请求内容为空",
    45110: "作者字数超限",
    45166: "内容不合法（可能含不允许的标签或外链）",
}
RETRY_CODES = {40014, 42001}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ApiError(Exception):
    def __init__(self, code: int, msg: str) -> None:
        self.code, self.msg = code, msg
        hint = ERRORS.get(code)
        super().__init__(f"微信接口错误 {code}：{msg}" + (f"\n处理：{hint}" if hint else ""))


def api_base() -> str:
    return os.environ.get("WECHAT_API_BASE", DEFAULT_BASE).rstrip("/")


def credentials() -> tuple[str, str]:
    app_id, secret = os.environ.get("WECHAT_APP_ID", ""), os.environ.get("WECHAT_APP_SECRET", "")
    if not app_id or not secret:
        raise SystemExit(
            "没有设置 WECHAT_APP_ID / WECHAT_APP_SECRET 环境变量（用 write_env_file 写入 .env，不要写进文章包或对话）"
        )
    return app_id, secret


def call(req: urllib.request.Request, timeout: float = 30) -> dict[str, object]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - base url is configured by the operator
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"接口返回 HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"连不上微信接口：{getattr(exc, 'reason', exc)}") from None
    except ValueError:
        raise SystemExit("接口返回的不是 JSON") from None
    if isinstance(data, dict) and data.get("errcode") not in (None, 0):
        raise ApiError(int(data["errcode"]), str(data.get("errmsg", "")))  # type: ignore[call-overload]
    return data  # type: ignore[no-any-return]


_TOKEN: dict[str, str] = {}


def get_token(force: bool = False) -> str:
    """access_token for this run; fetched once and reused until the API says it expired."""
    if _TOKEN.get("value") and not force:
        return _TOKEN["value"]
    app_id, secret = credentials()
    body = json.dumps(
        {
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": secret,
            "force_refresh": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{api_base()}/cgi-bin/stable_token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = call(req).get("access_token")
    if not token:
        raise SystemExit("没有拿到 access_token")
    _TOKEN["value"] = str(token)
    return _TOKEN["value"]


def multipart(field: str, path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    return (
        head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(),
        f"multipart/form-data; boundary={boundary}",
    )


def upload(url: str, path: Path) -> dict[str, object]:
    body, ctype = multipart("media", path)
    return call(
        urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST"),
        timeout=60,
    )


def with_retry(fn):  # type: ignore[no-untyped-def]
    """Run fn(token); on an expired token fetch a new one once."""
    token = get_token()
    try:
        return fn(token)
    except ApiError as exc:
        if exc.code not in RETRY_CODES:
            raise
        return fn(get_token(force=True))


# ------------------------------------------------------------------ render


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
        if re.match(r"^#{2,6}\s*(?:参考来源|参考资料|参考链接)\s*$", s):
            break
        body.append(line)
    return title, digest, body


def clean_markers(text: str) -> str:
    return re.sub(r"\s+([。！？，；：、）])", r"\1", MARK_RE.sub("", text))


def figure_path(fig: ap.Figure, figures_dir: Path) -> Path:
    return figures_dir / fig.fields.get("共享名", "")


def figure_problems(pack: ap.Pack, figures_dir: Path) -> list[str]:
    """Figures that are missing locally or differ from what the illustrator registered."""
    out = []
    for fig in pack.figures():
        path = figure_path(fig, figures_dir)
        if not fig.fields.get("共享名"):
            out.append(f"{fig.id} 没有共享名")
        elif not path.is_file():
            out.append(
                f'{fig.id} 还没取回：调用 shared_file_get(name="{fig.fields["共享名"]}")，默认放到 {path}'
            )
        elif ap.file_hash(path.read_bytes()) != fig.fields.get("哈希"):
            out.append(f"{fig.id} 的文件 {path} 和配图单登记的哈希不一致，不是预览时那张图")
    return out


def layout_markdown(
    pack: ap.Pack, author: str, digest: str, figures_dir: Path | None = None
) -> tuple[str, str, str, list[ap.Source]]:
    title, digest0, body = split_draft(pack.draft())
    digest = digest or digest0
    cited = {s.id: s for s in pack.sources()}
    used = [cited[i] for i in ap.citation_ids(pack.draft()) if i in cited]
    figures = [f for f in pack.figures() if f.position != "封面"]
    if figures_dir is not None and figures:
        body = ap.illustrated_lines(
            "\n".join(body), figures, lambda f: str(figure_path(f, figures_dir).resolve())
        )
    lines = [clean_markers(line) for line in body]
    meta = ["---", f"标题：{title}"]
    if author:
        meta.append(f"作者：{author}")
    if digest:
        meta.append(f"摘要：{digest}")
    meta.append("---")
    text = "\n".join(meta) + "\n" + "\n".join(lines).strip() + "\n"
    if used:
        text += "\n## 参考来源\n\n"
        for s in used:
            f = s.fields
            text += f"- {f.get('发布方', '')}《{f.get('标题', '')}》，{f.get('发布日期', '未知')}，{f.get('网址', '')}\n"
    credits = ap.figure_credits(pack.figures()) if figures_dir is not None else []
    if credits:
        text += "\n## 图片来源\n\n" + "".join(f"- {c}\n" for c in credits)
    return title, digest, text, used


def render_html(
    pack: ap.Pack, theme: str, author: str, digest: str, figures_dir: Path | None = None
) -> tuple[str, str, str, list[ap.Source]]:
    title, digest, md, used = layout_markdown(pack, author, digest, figures_dir)
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "a.md", Path(tmp) / "a.html"
        src.write_text(md, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(LAYOUT), str(src), "-o", str(out), "--fragment", "--theme", theme],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},  # noqa: S603
        )
        if proc.returncode != 0 or not out.is_file():
            raise SystemExit(f"排版失败：{(proc.stderr or proc.stdout).strip()}")
        return title, digest, out.read_text(encoding="utf-8"), used


IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')


def image_sources(content: str) -> list[str]:
    return [m.group(2) for m in IMG_SRC_RE.finditer(content)]


def resolve_image(src: str, base: Path, tmp: Path) -> Path:
    src = html.unescape(src)
    if re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 OctopPublisher"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = resp.read(10_000_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SystemExit(f"下载图片失败 {src}：{getattr(exc, 'reason', exc)}") from None
        name = Path(urllib.parse.urlparse(src).path).name or "image.jpg"
        path = tmp / name
        path.write_bytes(data)
        return path
    path = (base / src).resolve()
    if not path.is_file():
        raise SystemExit(f"找不到图片：{src}（相对于 {base}）")
    return path


# ------------------------------------------------------------------ ledger


def ledger_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def ledger_add(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.is_file()
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        if new:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in LEDGER_COLS})


def already_sent(path: Path, draft_hash: str) -> dict[str, str] | None:
    for r in ledger_rows(path):
        if r.get("初稿哈希") == draft_hash and r.get("状态") in ("已存草稿", "已发布"):
            return r
    return None


# ------------------------------------------------------------------ commands


def cmd_check(a: argparse.Namespace) -> int:
    app_id, secret = os.environ.get("WECHAT_APP_ID", ""), os.environ.get("WECHAT_APP_SECRET", "")
    print(f"WECHAT_APP_ID：{'已设置（' + app_id[:4] + '****）' if app_id else '未设置'}")
    print(f"WECHAT_APP_SECRET：{'已设置' if secret else '未设置'}")
    print(f"接口地址：{api_base()}")
    if not (app_id and secret):
        return 1
    if a.online:
        try:
            get_token(force=True)
        except ApiError as exc:
            print(f"取 access_token 失败：{exc}")
            return 3
        print("access_token 获取成功，凭据和 IP 白名单都没问题")
    else:
        print("（加 --online 可以真实试取一次 access_token）")
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    if "初稿" not in pack.sections:
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    fig_dir = Path(a.figures_dir) if pack.figures() else None
    if fig_dir is not None:
        problems = figure_problems(pack, fig_dir)
        if problems:
            print("配图没准备好：")
            for x in problems:
                print(f"- {x}")
            return 3
    title, digest, content, used = render_html(pack, a.theme, a.author, a.digest, fig_dir)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(
        f"已生成 {out}：标题“{title}”，摘要 {len(digest)} 字，参考来源 {len(used)} 条，HTML {len(content)} 字符"
    )
    print(
        "提示：这是正文片段，用于接口提交；要手动粘贴到公众号编辑器，请用写作专家的 wechat_layout.py 生成带预览页的版本。"
    )
    return 0


def cmd_draft(a: argparse.Namespace) -> int:
    path = Path(a.pack)
    pack = ap.read_pack(path)
    ok, reasons = ap.gate(pack, a.confirmed)
    if not ok:
        print("不能存草稿：")
        for r in reasons:
            print(f"- {r}")
        return 3
    draft_hash = ap.text_hash(pack.draft())
    ledger = Path(a.ledger)
    prev = already_sent(ledger, draft_hash)
    if prev and not a.force:
        print(
            f"这一版初稿（{draft_hash}）已经在 {prev['时间']} 存过草稿（media_id {prev['media_id']}），不重复提交；确需重来加 --force"
        )
        return 3
    fig_dir = Path(a.figures_dir) if pack.figures() else None
    if fig_dir is not None:
        problems = figure_problems(pack, fig_dir)
        if problems:
            print("配图没准备好，不能存草稿：")
            for x in problems:
                print(f"- {x}")
            return 3
    covers = [f for f in pack.figures() if f.position == "封面"]
    if a.cover:
        cover = Path(a.cover)
    elif covers and fig_dir is not None:
        cover = figure_path(covers[0], fig_dir)
    else:
        print("没有封面：配图单里没有位置为“封面”的图，也没有给 --cover", file=sys.stderr)
        return 1
    if not cover.is_file():
        print(f"找不到封面图：{cover}", file=sys.stderr)
        return 1
    base = Path(a.image_dir) if a.image_dir else path.resolve().parent
    title, digest, content, used = render_html(pack, a.theme, a.author, a.digest, fig_dir)
    images = image_sources(content)
    if not title:
        print("初稿没有标题", file=sys.stderr)
        return 1
    if a.dry_run:
        print("试运行：不联网，不写任何文件。将要做的事：")
        print(
            f"- 标题：{title}（{len(title)} 字）；作者：{a.author or '（空）'}；摘要：{len(digest)} 字"
        )
        print(
            f"- 封面：{cover}；正文 HTML {len(content)} 字符；图片 {len(images)} 张（配图单 {len(pack.figures())} 张）；参考来源 {len(used)} 条"
        )
        print(
            f"- 接口：{api_base()}（stable_token → {'material/add_material' if a.cover_type == 'image' else 'material/add_material(thumb)'} → media/uploadimg → draft/add）"
        )
        print(f"- 成功后写入：文章包“发布单”、台账 {ledger}")
        return 0
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        try:
            cover_id = str(
                with_retry(
                    lambda t: upload(
                        f"{api_base()}/cgi-bin/material/add_material?access_token={t}&type={a.cover_type}",
                        cover,
                    )
                )["media_id"]
            )
            for src in dict.fromkeys(images):
                if re.match(r"^https?://mmbiz\.qpic\.cn/", src):
                    continue
                local = resolve_image(src, base, tmp)
                url = str(
                    with_retry(
                        lambda t, p=local: upload(
                            f"{api_base()}/cgi-bin/media/uploadimg?access_token={t}", p
                        )
                    )["url"]
                )
                content = content.replace(f'src="{src}"', f'src="{url}"')
            article = {
                "title": title, "author": a.author, "digest": digest, "content": content,
                "content_source_url": "", "thumb_media_id": cover_id,
                "need_open_comment": 1 if a.comment else 0, "only_fans_can_comment": 0,
            }  # fmt: skip
            payload = json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8")

            def add(token: str) -> dict[str, object]:
                req = urllib.request.Request(
                    f"{api_base()}/cgi-bin/draft/add?access_token={token}",
                    data=payload,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                return call(req)

            media_id = str(with_retry(add)["media_id"])
        except ApiError as exc:
            print(str(exc))
            ledger_add(
                ledger,
                {
                    "时间": now(),
                    "话题": pack.topic,
                    "平台": "微信公众号",
                    "状态": "失败",
                    "初稿哈希": draft_hash,
                    "标题": title,
                    "说明": f"{exc.code} {exc.msg}",
                },
            )
            return 3
    stamp = now()
    pack.sections["发布单"] = "\n".join([
        "- 平台：微信公众号", "- 状态：已存草稿", f"- media_id：{media_id}", f"- 时间：{stamp}",
        f"- 对应初稿：{draft_hash}", "- 说明：只存入了草稿箱，需要到公众号后台预览、设置群发后手动发布",
    ])  # fmt: skip
    ap.write_pack(path, pack)
    ledger_add(
        ledger,
        {
            "时间": stamp,
            "话题": pack.topic,
            "平台": "微信公众号",
            "状态": "已存草稿",
            "media_id": media_id,
            "初稿哈希": draft_hash,
            "标题": title,
            "说明": "存入草稿箱",
        },
    )
    print(f"已存入草稿箱：media_id {media_id}")
    print("下一步：到公众号后台“内容与互动 → 草稿箱”预览，确认无误后由你手动发布。")
    return 0


def cmd_figures(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    figures = pack.figures()
    if not figures:
        print("文章包里没有配图单：存草稿时要用 --cover 指定封面")
        return 0
    fig_dir = Path(a.figures_dir)
    for fig in figures:
        print(
            f"- {fig.id}（{fig.position}，{fig.mode}）共享名 {fig.fields.get('共享名', '')} → {figure_path(fig, fig_dir)}"
        )
    problems = figure_problems(pack, fig_dir)
    if problems:
        print("还要处理：")
        for x in problems:
            print(f"- {x}")
        return 3
    print("配图都已取回，哈希和配图单一致")
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    pack = ap.read_pack(Path(a.pack))
    if "初稿" not in pack.sections:
        print("文章包里还没有初稿", file=sys.stderr)
        return 1
    ok, reasons = ap.gate(pack, True)
    if not ok:
        print("不能导出：")
        for r in reasons:
            print(f"- {r}")
        return 3
    title, digest, body = split_draft(pack.draft())
    text = clean_markers("\n".join(body)).strip()
    limits = {"xiaohongshu": (20, 1000), "toutiao": (30, None), "plain": (None, None)}[a.platform]
    out = (
        [title, "", text]
        if a.platform != "xiaohongshu"
        else [title, "", re.sub(r"(?m)^#{2,6}\s*", "", text)]
    )
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    print(f"已导出 {a.output}：标题 {len(title)} 字，正文 {len(re.sub(r'\s', '', text))} 字")
    if limits[0] and len(title) > limits[0]:
        print(f"提示：标题超过 {a.platform} 的 {limits[0]} 字，需要改短后重新核验")
    if limits[1] and len(re.sub(r"\s", "", text)) > limits[1]:
        print(f"提示：正文超过 {a.platform} 的 {limits[1]} 字，需要精简后重新核验")
    print("说明：这个平台没有稳定的官方接口，请自行复制发布；发布后用 ledger add 记一笔。")
    return 0


def cmd_ledger(a: argparse.Namespace) -> int:
    path = Path(a.ledger)
    if a.action == "list":
        rows = ledger_rows(path)
        for r in rows:
            print(
                f"{r['时间']}  {r['平台']}  {r['状态']}  {r['标题']}  {r['media_id']}  {r['初稿哈希']}"
            )
        if not rows:
            print("台账还是空的")
        return 0
    if a.action == "check":
        if not a.hash:
            print("check 需要 --hash", file=sys.stderr)
            return 1
        hit = already_sent(path, a.hash)
        print(f"已发过：{hit['时间']} {hit['平台']} {hit['状态']}" if hit else "没有发过")
        return 3 if hit else 0
    if not (a.platform and a.state and a.title):
        print("add 需要 --platform --state --title", file=sys.stderr)
        return 1
    ledger_add(
        path,
        {
            "时间": now(),
            "话题": a.topic,
            "平台": a.platform,
            "状态": a.state,
            "media_id": a.media_id,
            "初稿哈希": a.hash,
            "标题": a.title,
            "说明": a.note,
        },
    )
    print("已记入台账")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="公众号草稿箱")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--online", action="store_true")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("render")
    r.add_argument("pack")
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--theme", default="green", choices=["green", "blue", "orange"])
    r.add_argument("--author", default="")
    r.add_argument("--digest", default="")
    r.add_argument("--figures-dir", default="shared")
    r.set_defaults(fn=cmd_render)
    d = sub.add_parser("draft")
    d.add_argument("pack")
    d.add_argument("--cover", default="")
    d.add_argument("--cover-type", default="image", choices=["image", "thumb"])
    d.add_argument("--confirmed", action="store_true")
    d.add_argument("--author", default="")
    d.add_argument("--digest", default="")
    d.add_argument("--theme", default="green", choices=["green", "blue", "orange"])
    d.add_argument("--comment", action="store_true", help="开启留言")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--force", action="store_true")
    d.add_argument("--image-dir")
    d.add_argument("--figures-dir", default="shared", help="shared_file_get 取回配图的目录")
    d.add_argument("--ledger", default="outputs/publish-ledger.csv")
    d.set_defaults(fn=cmd_draft)
    fg = sub.add_parser("figures")
    fg.add_argument("pack")
    fg.add_argument("--figures-dir", default="shared")
    fg.set_defaults(fn=cmd_figures)

    e = sub.add_parser("export")
    e.add_argument("pack")
    e.add_argument("--platform", required=True, choices=["xiaohongshu", "toutiao", "plain"])
    e.add_argument("-o", "--output", required=True)
    e.set_defaults(fn=cmd_export)
    lg = sub.add_parser("ledger")
    lg.add_argument("action", choices=["list", "check", "add"])
    lg.add_argument("--ledger", default="outputs/publish-ledger.csv")
    lg.add_argument("--hash", default="")
    lg.add_argument("--topic", default="")
    lg.add_argument("--platform", default="")
    lg.add_argument("--state", default="")
    lg.add_argument("--media-id", default="")
    lg.add_argument("--title", default="")
    lg.add_argument("--note", default="")
    lg.set_defaults(fn=cmd_ledger)
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
    except ApiError as exc:
        print(str(exc))
        return 3


if __name__ == "__main__":
    sys.exit(main())
