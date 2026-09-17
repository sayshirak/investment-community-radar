---
name: investment-community-radar
description: 从 Reddit 八个个人投资社区、V2EX 指定节点与 Hacker News（front/ask/show）中筛选高回复热点新闻与长尾故事，生成带原帖链接的中文材料清单。Use when the user asks 投资故事、Reddit/V2EX/HN 热帖、个人投资经历、投资社区热点、行为金融选题、热点素材雷达、查找国内外投资故事。
---

# Reddit + V2EX + Hacker News 故事雷达

从 Reddit、V2EX 与 Hacker News 只读抓取公开讨论，按回复数与发帖窗口筛选「热点新闻」和「长尾故事」。不要求同一话题跨社区出现，不做事件聚类，不自动映射心理学效应。

## 快速执行

在本 skill 目录运行：

```bash
python scripts/run_radar.py status
python scripts/run_radar.py collect
python scripts/run_radar.py all
```

- `collect`：只写入 `output/latest/hotspots.json`（推荐被其他 skill 编排调用）
- `all`：collect + 生成并打印 `weekly_hotspots.md`
- `status`：探活各源（失败应先修网络/代理再 collect）

Reddit 全时段评论 P95 缓存默认 **24 小时 TTL**（`config.json` → `reddit.percentile_cache_ttl_hours`）；未过期则跳过 Arctic Shift 重算。

产物：

- `output/latest/hotspots.json`：结构化结果
- `output/latest/weekly_hotspots.md`：中文阅读版（`all` / `report`）

仅抓某个平台：

```bash
python scripts/run_radar.py collect --only reddit
python scripts/run_radar.py all --only v2ex
python scripts/run_radar.py all --only hackernews
```

## 来源

Reddit：

- `r/wallstreetbets`
- `r/personalfinance`
- `r/investing`
- `r/Bogleheads`
- `r/financialindependence`
- `r/ValueInvesting`
- `r/stocks`
- `r/dividends`

过滤掉标题以 `Daily FI discussion thread` 开头的日常汇总帖（不区分大小写）。

V2EX 主节点（报告优先展示）：

- `/go/invest`
- `/go/stock`
- `/go/bitcoin`

V2EX 扩展来源：

- `/go/programmer`
- `/go/business`
- `/go/mileage`
- `/go/career`
- `/go/qna`
- `/go/life`
- `/go/create`
- `/recent`

Hacker News：

- `/front`（按日回溯近一个月；外链会抓取网页摘要写入 `summary`）
- `/ask`（Algolia `ask_hn`）
- `/show`（Algolia `show_hn`）

尽量排除纯科技/工程讨论：标题或摘要必须带有投资/决策信号才保留；短关键词（如 `ira`）按词边界匹配，避免误伤。

V2EX 直接解析公开 HTML。Reddit 先尝试官方公开 JSON；遇到整网 403 时降级到 Arctic Shift 公开归档 API。归档是只读数据源，不是代理或反爬绕过。

V2EX 额外排除开户广告帖（如低佣开户 / 免五 / `promotions` 节点）和卖 AI 中转站 / API Key 的推广帖。

## 唯一入选规则

### Reddit

1. **热点新闻**：发帖落在近 7 个自然日（例如今天是 2026-07-07，则含 2026-07-01 至 2026-07-07），且评论数严格大于 `max(50, 该 subreddit 全时段 P95)`。
2. **长尾故事**：评论数严格大于 `max(100, 该 subreddit 全时段 P95)`，不限制发帖日期。

每次 `collect` 会按年分层抽样刷新各板 P95，并缓存到 `output/reddit_percentiles.json`；刷新失败时回退缓存，再不行则仅用 floor（50 / 100）。

### V2EX

1. **热点新闻**：发帖落在近 7 个自然日，且回复数严格大于 100。
2. **长尾故事**：回复数严格大于 1000，不限制发帖日期。

### Hacker News

1. **热点新闻**：发帖落在近 30 个自然日，且回复数严格大于 100。
2. **长尾故事**：发帖落在近 30 个自然日，且回复数严格大于 1000。

若同时满足两条，归入长尾故事。不要求 24 小时内有新回复，也不要求跨社区同题。

## 执行流程

1. 抓列表，先用回复数缩小候选池。
2. V2EX 访问详情页确认发帖时间；Reddit 使用帖子自带评论数与发帖时间；Hacker News 使用 Algolia / `/front` 列表时间，并对 `/front` 外链做网页摘要。
3. 应用入选规则；URL 去重。
4. 用标题与正文摘要做非门槛式材料分类。
5. 为 Reddit / Hacker News 入选帖生成中文题目和中文摘要；翻译失败不影响入选。
6. 按平台、`long_tail`/`recent`、回复数排序并生成报告。

## 输出要求

每条材料必须包含：

- 标题、平台、板块/节点、材料类型
- Reddit / Hacker News 额外包含 `title_zh`（中文题目）和 `summary_zh`（中文摘要）
- 发帖时间、最后回复时间、回复数
- 入选类型：`recent` 或 `long_tail`
- 规则命中说明、正文摘要、原帖链接

来源失败必须写入覆盖缺口；不得用旧数据或记忆补齐。

## 安全边界

- 只读、低频、串行访问；不破验证码、不使用代理绕过封锁。
- 不保存 Cookie、用户名列表或评论原文。
- 报告只保存主帖公开摘要、计数、时间与链接。
- Reddit / Hacker News 公开标题和摘要会分开发送到免密翻译端点（独立客户端；可用 `translation.proxy` 只给翻译走代理；主端点失败时尝试 fallback_endpoints）；可在 `config.json` 关闭。
- Cookie 若未来需要，只能从环境变量读取。

详细字段见 [references/fields.md](references/fields.md)，来源维护见 [references/sources.md](references/sources.md)。
