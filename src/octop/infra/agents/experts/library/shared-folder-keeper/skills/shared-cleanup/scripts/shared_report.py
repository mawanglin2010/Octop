#!/usr/bin/env python3
"""汇总共享文件夹清单，挑出可以清理的文件。只读，不删除任何东西。

先调用 shared_file_list，把返回的 JSON 原样存成文件（如 outputs/shared-list.json），再：

Usage:
    python scripts/shared_report.py outputs/shared-list.json                          按文件夹汇总数量、大小、最新和最旧时间
    python scripts/shared_report.py outputs/shared-list.json --older-than 30          列出 30 天没动过的文件
    python scripts/shared_report.py outputs/shared-list.json --prefix 充电桩补贴/      只看某个文件夹
    python scripts/shared_report.py outputs/shared-list.json --pattern "*.pdf" --json  按文件名通配，输出 JSON

输出的“候选”只是建议，删除前必须让用户确认，然后逐个调用 shared_file_delete(name=…)。
Exit codes: 0 正常, 1 输入错误。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读不了 {path}：{exc}") from None
    if isinstance(data, dict) and "error" in data:
        raise SystemExit(f"shared_file_list 返回了错误：{data['error']}")
    rows = data.get("files") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise SystemExit("不是 shared_file_list 的输出（缺少 files 列表）")
    if isinstance(data, dict) and data.get("truncated"):
        print(
            "提示：清单被截断了（超过 500 个文件），用 shared_file_list(prefix=…) 分文件夹再看",
            file=sys.stderr,
        )
    out = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("name"), str):
            out.append(r)
    return out


def parse_time(raw: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="共享文件夹清单汇总")
    p.add_argument("listing")
    p.add_argument("--older-than", type=int, default=0, help="只列出超过 N 天没修改的文件")
    p.add_argument("--prefix", default="")
    p.add_argument("--pattern", default="", help="文件名通配，如 *.pdf")
    p.add_argument("--now", default="", help="以这个时间为“现在”（ISO 格式，测试用）")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        rows = load(Path(a.listing))
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)
        return 1
    now = parse_time(a.now) if a.now else datetime.now(UTC)
    if now is None:
        print("--now 不是合法的时间", file=sys.stderr)
        return 1
    picked: list[dict[str, Any]] = []
    for r in rows:
        name = str(r["name"])
        if a.prefix and not name.startswith(a.prefix):
            continue
        if a.pattern and not fnmatch.fnmatch(name.rsplit("/", 1)[-1], a.pattern):
            continue
        t = parse_time(r.get("modified"))
        age = (now - t).days if t else None
        if a.older_than and (age is None or age < a.older_than):
            continue
        picked.append(
            {
                "name": name,
                "size": int(r.get("size") or 0),
                "modified": r.get("modified"),
                "days": age,
            }
        )

    groups: dict[str, dict[str, Any]] = {}
    for r in picked:
        top = r["name"].split("/", 1)[0] if "/" in r["name"] else "（根目录）"
        g = groups.setdefault(
            top, {"folder": top, "files": 0, "size": 0, "newest": None, "oldest": None}
        )
        g["files"] += 1
        g["size"] += r["size"]
        m = str(r["modified"] or "")
        if m:
            g["newest"] = max(filter(None, [g["newest"], m]))
            g["oldest"] = min(filter(None, [g["oldest"], m]))
    total = sum(r["size"] for r in picked)
    if a.json:
        print(json.dumps({"files": picked, "folders": list(groups.values()), "total_size": total},
                         ensure_ascii=False, indent=2))  # fmt: skip
        return 0
    cond = []
    if a.older_than:
        cond.append(f"超过 {a.older_than} 天未修改")
    if a.prefix:
        cond.append(f"在 {a.prefix} 下")
    if a.pattern:
        cond.append(f"文件名匹配 {a.pattern}")
    print(
        f"{'、'.join(cond) or '全部'}：{len(picked)} 个文件，共 {human(total)}（清单共 {len(rows)} 个）"
    )
    for g in sorted(groups.values(), key=lambda x: -x["size"]):
        print(
            f"- {g['folder']}：{g['files']} 个，{human(g['size'])}，最旧 {g['oldest'] or '-'}，最新 {g['newest'] or '-'}"
        )
    if a.older_than and picked:
        print("候选（需用户确认后再逐个 shared_file_delete）：")
        for r in sorted(picked, key=lambda x: str(x["name"])):
            print(f"  {r['name']}  {human(r['size'])}  {r['days']} 天")
    return 0


if __name__ == "__main__":
    sys.exit(main())
