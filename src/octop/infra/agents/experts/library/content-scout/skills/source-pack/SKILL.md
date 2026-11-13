---
name: source-pack
description: "登记和整理线索包：source_pack.py 抓取网页并按关键词逐字取原句作为摘录（fetch）、手工登记来源（add）、检查每条链接能否打开并写回结果（check）、按网址和摘录相似度去重（dedupe）、按发布日期排时间线（timeline）、列出来源（list）；article_pack.py 建立和校验整份文章包（选题单、线索包、初稿、核验报告、配图单、发布单）。收集好的线索要交给撰稿人时使用。"
metadata:
  octop:
    emoji: "🧾"
    label:
      zh: "线索包"
      en: "Source Pack"
    summary:
      zh: "抓取并逐字摘录、检查链接、去重、整理时间线，维护文章包。"
      en: "Fetch with verbatim excerpts, check links, dedupe, build timelines, maintain the article pack."
---

# 线索包

> `scripts/` 路径相对于本技能目录。

整个内容团队用同一份“文章包”交接工作：选题单 → 线索包 → 初稿 → 核验报告 → 配图单 → 发布单。你负责前两节。格式和各字段含义见 `scripts/article_pack.py` 开头的说明。

## 1. 建文章包

```bash
python scripts/article_pack.py new outputs/pack/充电桩补贴.md --topic "示例市充电桩补贴" \
    --platform 微信公众号 --audience "有意建桩的商户" --length "1000 字" --avoid "不预测是否加码"
```

## 2. 登记来源

**能抓取的页面**（推荐，摘录是页面里的原句，不会走样）：

```bash
python scripts/source_pack.py fetch https://example.gov.cn/notice --match 补贴 --match 日期      # 先看候选原句
python scripts/source_pack.py fetch https://example.gov.cn/notice --match 补贴 \
    --add-to outputs/pack/充电桩补贴.md --credibility A                                        # 满意就直接登记
```

`--match` 可重复，取含任一关键词的句子；`--max` 控制最多几句；页面里找不到发布日期时会写“未知”，请到页面上确认后手工补。

**抓不到的页面**（要登录、靠脚本渲染、PDF）：用 `browser_use` 打开，把原文逐字复制，然后：

```bash
python scripts/source_pack.py add outputs/pack/充电桩补贴.md --title "某某通知" --url https://… \
    --publisher 示例市发改委 --date 2026-10-20 --credibility A --status 可用 \
    --excerpt "第一句原文" --excerpt "第二句原文" --note "据 PDF 第 2 页转写"
```

可用和存疑的来源必须有摘录；不可用的来源可以没有摘录，但要在 `--note` 里写原因。

## 3. 整理

```bash
python scripts/source_pack.py check outputs/pack/充电桩补贴.md --write   # 链接能否打开，结果写进来源的“链接检查”字段
python scripts/source_pack.py dedupe outputs/pack/充电桩补贴.md --mark   # 同网址或摘录高度相似的，标成重复
python scripts/source_pack.py timeline outputs/pack/充电桩补贴.md        # 按发布日期排时间线
python scripts/source_pack.py list outputs/pack/充电桩补贴.md
python scripts/article_pack.py validate outputs/pack/充电桩补贴.md       # 结构检查，修到没有错误
```

- `check` 退出码 3 表示有链接打不开：确认是临时故障还是已下线；已下线的内容不能当来源，改状态为“不可用”并写备注。
- `dedupe` 找出的重复在“有几个独立来源”的判断里只算一条。
- 抓取只用普通 HTTP，不执行脚本、不登录；遇到访问限制不要想办法绕过。

## 4. 交付

```bash
python scripts/article_pack.py get outputs/pack/充电桩补贴.md 线索包      # 打印线索包全文，贴进回复里
python scripts/article_pack.py show outputs/pack/充电桩补贴.md             # 一屏摘要：来源数、状态分布
```

回复里必须贴出线索包全文：团队成员的工作区互相看不见，主持人只能转述你回复里的文字。
