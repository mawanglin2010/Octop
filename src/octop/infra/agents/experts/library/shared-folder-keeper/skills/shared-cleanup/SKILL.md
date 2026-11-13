---
name: shared-cleanup
description: "查看和清理共享文件夹：shared_file_list 取清单并存成 JSON，shared_report.py 按文件夹汇总文件数、大小、最旧和最新修改时间，按天数（--older-than）、文件夹（--prefix）、文件名通配（--pattern）挑出清理候选；用户确认后 shared_file_delete 逐个删除。用户问共享文件夹里有什么、占多大、要清理时使用。"
metadata:
  octop:
    emoji: "🧹"
    label:
      zh: "共享文件夹清理"
      en: "Shared Folder Cleanup"
    summary:
      zh: "汇总共享文件夹、挑出候选、确认后删除。"
      en: "Summarise the shared folder, pick candidates, delete after confirmation."
---

# 共享文件夹清理

> `scripts/` 路径相对于本技能目录。

共享文件夹是同一用户的专家交接文件的地方（`~/.octop/users/<用户名>/shared/`），不会自动清理。内容团队按话题分文件夹：`<话题>/文章包.md`、`<话题>/P1.jpg` 等。

## 1. 取清单

调用 `shared_file_list`（可传 `prefix` 只看一个文件夹），返回：

```json
{"files": [{"name": "充电桩补贴/P1.jpg", "size": 182334, "modified": "2026-11-01T08:00:00+00:00"}], "truncated": false}
```

原样存成 `outputs/shared-list.json`。`truncated` 为 true 说明超过 500 个文件，按文件夹分批取。

## 2. 汇总和挑候选

```bash
python scripts/shared_report.py outputs/shared-list.json                     # 按文件夹汇总
python scripts/shared_report.py outputs/shared-list.json --older-than 30     # 30 天没动过的候选
python scripts/shared_report.py outputs/shared-list.json --prefix 充电桩补贴/ --pattern "*.pdf"
python scripts/shared_report.py outputs/shared-list.json --older-than 30 --json
```

时间都是 UTC。脚本只读，不删除任何东西。

## 3. 确认和删除

1. 把候选按文件夹列给用户：文件夹、文件数、大小、多久没动；
2. 用户明确确认后，逐个 `shared_file_delete(name=…)`；空文件夹会自动去掉；
3. 删除不可恢复。正在等发布的文章包和它的配图（同一个话题文件夹）不要删，除非用户说这篇不发了。
