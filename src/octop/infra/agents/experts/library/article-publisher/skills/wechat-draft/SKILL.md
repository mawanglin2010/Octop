---
name: wechat-draft
description: "把核验通过的文章包存进微信公众号草稿箱：wechat_draft.py 检查 AppID/AppSecret 环境变量和接口可达性（check）、渲染排版 HTML 并去掉来源标记、文末生成参考来源（render）、按配图单插入配图（figures 检查从共享文件夹取回的图和哈希）、把关后上传封面和正文图片并新建草稿、写回发布单和台账（draft，支持 --dry-run）、导出小红书和头条的可复制文本（export）、查看和登记发布台账（ledger，防重复发布）。附微信接口说明和错误码处理（references/wechat-api.md）。确认发布后使用。"
metadata:
  octop:
    emoji: "📮"
    label:
      zh: "公众号草稿箱"
      en: "WeChat Draft"
    summary:
      zh: "排版、上传封面和图片、存草稿，写发布单和台账。"
      en: "Render, upload cover and images, save as draft, write the publish record and ledger."
---

# 公众号草稿箱

> `scripts/`、`references/` 路径相对于本技能目录。

只存草稿，不群发。接口细节按微信开放文档编写，没有用真实公众号验证；遇到权限问题的处理见 `references/wechat-api.md`。

## 1. 先检查环境

```bash
python scripts/wechat_draft.py check            # 凭据是否设置（不显示密钥）、接口地址
python scripts/wechat_draft.py check --online   # 真实试取一次 access_token，验证凭据和 IP 白名单
```

凭据：`WECHAT_APP_ID`、`WECHAT_APP_SECRET`。用 `write_env_file` 写入 `.env`，不要让用户把密钥贴到对话里，也不要写进文章包。

## 2. 渲染预览

```bash
python scripts/wechat_draft.py render 文章包.md -o outputs/wechat/文章.html --theme green --author 小编
```

渲染做的事：取初稿的标题、摘要，去掉 `[S1]`、`[推算]` 标记，按配图单把配图插到对应位置（生成的图图注带“（AI 生成）”），调用 `wechat_layout.py` 排版成内联样式 HTML，在文末按引用顺序生成“参考来源”（发布方、标题、日期、网址）和“图片来源”（搜索来的图的署名、授权、来源页）。输出的是接口提交用的正文片段；要手动粘贴到公众号编辑器，请用公众号写作专家的排版脚本生成带预览页的版本。

## 3. 取回配图

文章包里有“配图单”时（推文配图专家登记的），图片在共享文件夹里，先取回：

```bash
python scripts/wechat_draft.py figures 文章包.md        # 列出每张图的共享名和应放的位置，检查哈希
```

按列出的共享名逐个调用 `shared_file_get(name=共享名)`（默认写到 `shared/<共享名>`，和 `--figures-dir` 默认值一致），再跑一次 `figures`，看到“配图都已取回，哈希和配图单一致”才继续。哈希不一致说明不是预览时那张图，退回配图专家。

## 4. 存草稿

```bash
python scripts/wechat_draft.py draft 文章包.md --author 小编 --dry-run          # 先试运行，不联网不写文件
python scripts/wechat_draft.py draft 文章包.md --author 小编 --confirmed        # 用户确认后正式执行
```

| 参数 | 作用 |
|------|------|
| `--cover` | 封面图；有配图单时默认用其中位置为“封面”的图，没有配图单时必填；默认按永久素材 `image` 上传，`--cover-type thumb` 改按缩略图上传 |
| `--figures-dir` | 取回配图的目录，默认 `shared` |
| `--author` / `--digest` | 作者；摘要（默认取初稿的“摘要：”行） |
| `--theme` | 排版主题 green / blue / orange |
| `--comment` | 开启留言 |
| `--image-dir` | 正文里本地图片的目录，默认是文章包所在目录 |
| `--ledger` | 台账路径，默认 `outputs/publish-ledger.csv` |
| `--force` | 同一版初稿已存过草稿时仍然提交（需要用户明确要求） |

流程：把关（核验通过 + 初稿未改动 + 用户确认）→ 查台账防重复 → 渲染 → 取 token → 上传封面 → 上传正文图片并替换地址 → 新建草稿 → 写回文章包的“发布单” → 记台账。任何一步失败，都不会写“已存草稿”；接口报错会在台账里记一行“失败”。

## 5. 其他平台

```bash
python scripts/wechat_draft.py export 文章包.md --platform xiaohongshu -o outputs/xhs/文章.txt
```

支持 `xiaohongshu`、`toutiao`、`plain`：去掉来源标记，输出标题和正文，并提示超出该平台常见限制的地方。这些平台没有稳定的官方接口，只提供可复制的文本，由用户自己发布；发布后用 `ledger add` 记一笔。

## 6. 台账

```bash
python scripts/wechat_draft.py ledger list [--ledger 路径]
python scripts/wechat_draft.py ledger check --hash sha256:… [--ledger 路径]    # 已发过退出码为 3
python scripts/wechat_draft.py ledger add --platform 小红书 --state 已发布 --title "…" --hash sha256:… [--note …]
```

台账是 CSV：时间、话题、平台、状态、media_id、初稿哈希、标题、说明。同一版初稿（哈希相同）只要有“已存草稿”或“已发布”记录，`draft` 默认拒绝再提交。
