---
name: figure-pack
description: "登记配图和出预览：figures.py 列出初稿的段落编号和小标题并给出配图建议（plan）、登记生成的图（add，可按 2.35:1 等比例裁剪）、下载并登记搜索来的图（fetch，记录图片地址、来源页、署名、授权）、移除（remove）、检查配图单、图片文件和哈希（check）、列出要放进共享文件夹的文件（share）；preview_pdf.py 把初稿和配图排成预览页导出 PDF（附参考来源、图片来源和配图清单）。配图、出预览、交给发布专员时使用。"
metadata:
  octop:
    emoji: "🖼️"
    label:
      zh: "配图单与预览"
      en: "Figures & Preview"
    summary:
      zh: "登记配图、检查授权和哈希、导出预览 PDF、经共享文件夹交接。"
      en: "Register figures, check licences and hashes, export a preview PDF, hand off via the shared folder."
---

# 配图单与预览

> `scripts/` 路径相对于本技能目录。

配图记在文章包的“配图单”一节，格式见 `scripts/article_pack.py` 开头的说明。配图**不改初稿**：预览和发布时才按“位置”把图插进正文，所以核验报告不会因为配图过期。

## 1. 规划位置

```bash
python scripts/figures.py plan outputs/pack/充电桩补贴.md
```

列出“第1段、第2段……”和小标题。位置只能写：

| 位置 | 含义 |
|------|------|
| `封面` | 公众号封面，只能一张，不出现在正文里 |
| `开头` | 标题下面、正文第一段前 |
| `第3段后` | 第 3 个正文段落后面（编号以 plan 为准） |
| `「补贴多少」后` | 这个小标题后面 |

## 2. 登记

```bash
# 生成的图：先调用 generate_image，拿到工作区里的路径
python scripts/figures.py add 文章包.md --file generated/images/xxx.jpg --mode 生成 \
    --prompt "城市街头的电动车充电桩，扁平插画，蓝绿色调，横版构图" --model "doubao-seedream-5.0-flash" \
    --pos 封面 --caption "城市街头的充电桩" --crop 2.35:1

# 搜索来的图：下载并登记
python scripts/figures.py fetch 文章包.md --url https://…/photo.jpg --page https://…/photo-page \
    --credit "示例市发改委" --license "官方发布，允许转载" --pos 第2段后 --caption "充电站现场"
```

- 图片复制到 `images/<话题>/P1.jpg`，共享名是 `<话题>/P1.jpg`，同时记下文件哈希；
- 同一编号再 add 一次就是替换（`--id P2`）；`remove 文章包.md P2` 移除；
- 只收 JPG、PNG、GIF、WebP，单张不超过 15 MB；
- 生成的图，图注在预览和发布时自动加“（AI 生成）”。

## 3. 检查

```bash
python scripts/figures.py check 文章包.md          # 退出码 3 表示有错误
```

检查：必填项、位置是否存在、只能一张封面、搜索图的授权（“未知”“不明”直接判错）、网址格式、图片文件是否还在、内容是否和登记时一致（哈希）。图注里有数字会提示：图注只描述画面。

## 4. 预览 PDF

```bash
python scripts/preview_pdf.py 文章包.md --author 小编 [--theme green] [-o outputs/preview/充电桩补贴.pdf]
```

- 按公众号排版样式排版，去掉 `[S1]` 标记，封面放最前，配图插到对应位置；页首标“预览稿，未发布”和核验结论，文末列参考来源、图片来源和配图清单；
- 同时生成同名 `.html`，用浏览器也能看；
- PDF 用无头 Chromium 打印。退出码 2 表示没装 Chromium（设置 → 浏览器里安装），此时只有 HTML；
- 机器上没有中文字体时会提示，PDF 里的中文可能是方框，HTML 不受影响。

## 5. 交给发布专员

```bash
python scripts/figures.py share 文章包.md
```

按列出的“文件 → 共享名”逐个调用 `shared_file_put(path=文件, name=共享名, overwrite=true)`，再把文章包本身放进去：`shared_file_put(path=文章包, name="<话题>/文章包.md", overwrite=true)`。发布专员用同样的共享名取回，存草稿前会核对哈希，图片被换过就会拒绝。
