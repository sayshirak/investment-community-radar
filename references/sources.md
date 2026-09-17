# 来源维护

## Reddit

正式板块：

`wallstreetbets`、`personalfinance`、`investing`、`Bogleheads`、
`financialindependence`、`ValueInvesting`、`stocks`、`dividends`。

`exclude_title_prefixes` 在入选前排除固定栏目。默认排除
`Daily FI discussion thread`，匹配不区分大小写并允许标题后面带日期。

流程：

1. 探测官方 `www` / `old` / `api.reddit.com` JSON。
2. 官方可用时读取 `new` / `top(week|year|all)` / `hot`，用帖子自带
   `num_comments` 与 `created_utc` 入选。
3. 每次 collect 先刷新各 subreddit 全时段评论数 P95（Arctic Shift 按年
   分层抽样：每年最早/最晚各最多 100 帖），写入
   `output/reddit_percentiles.json`。
4. 入选门槛：
   - 热点：近 7 日，且 `num_comments > max(50, P95)`
   - 长尾：`num_comments > max(100, P95)`，不限发帖日期
5. 官方 403 时使用 Arctic Shift `/api/posts/search`：
   - 近 7 日窗口分页找热点候选
   - 更长回看窗口分页找长尾候选

归档扫描页数由 `archive_hot_pages`、`archive_long_tail_pages`、
`archive_long_tail_lookback_days` 控制；这是覆盖与低频访问之间的边界，
不是对 Reddit 全站穷举。

归档没有稳定性承诺。失败时在报告写覆盖缺口，不使用旧结果补齐。

### Reddit 中文字段

入选后才将 Reddit 的公开标题与摘要发送到 `config.json` 中的免密翻译端点，
生成 `title_zh` 和 `summary_zh`。翻译缓存写入
`output/translation_cache.json`，只保存内容哈希和中文结果，不保存 Cookie。
翻译连续失败 3 次会停止本轮后续请求；热点仍保留，中文字段为空。

## V2EX

主节点：`invest`、`stock`、`bitcoin`。

扩展节点：`programmer`、`business`、`mileage`、`career`、`qna`、
`life`、`create`，另抓 `/recent`。

列表解析：

- `a.topic-link`：标题与主题 URL
- `a.count_livid`：回复数
- `span.ago[title]`：列表最新回复时间
- `a.node`：`/recent` 中的真实节点

详情解析：

- `.header small.gray span[title]`：发帖时间
- `.cell[id^="r_"] span.ago[title]`：回复时间
- `.topic_content`：主帖摘要

列表页先按回复数 > 100 预筛，再拉详情确认发帖时间。
长尾看回复数 > 1000，不限发帖日期。
默认每节点多页、`/recent` 两页；页数可在 `config.json` 调整。

广告过滤（`config.json` 的 `v2ex` 段）：

- `exclude_communities`：默认排除 `promotions`
- `exclude_brokerage_ad_terms`：低佣开户、免五、开户红包等
- `exclude_ai_relay_ad_terms`：中转站、API 中转、成品号、官网 key 等

普通提问（如“怎么开 Codex 会员”）不会仅因提到产品名被误杀。

## Hacker News

板块：

- `/front`：按 `?day=YYYY-MM-DD` 回溯近 30 个自然日；评论数 > 100 才进入候选
- `/ask`：Algolia `tags=ask_hn`
- `/show`：Algolia `tags=show_hn`

入选：

- 热点：近 30 日发帖，且评论数 > 100
- 长尾：近 30 日发帖，且评论数 > 1000

`/front` 外链会抓取目标网页的 title / meta description / 前几段正文，写入 `summary`（最多 500 字符）。抓取失败时保留失败说明，不阻断入选。

主题过滤（尽量排除科技讨论）：

- 标题或摘要必须含投资/决策关键词才保留
- 短词（如 `ira`、`tax`）按词边界匹配
- 词表见 `config.json` 的 `hackernews.invest_decision_terms` 与
  `hackernews.exclude_tech_terms`

Reddit / Hacker News 入选后会尝试生成 `title_zh` / `summary_zh`。

## 故障分类

- `http_403`：站点拒绝访问
- `http_429`：限流
- `http_4xx` / `http_5xx`：HTTP 错误
- `timeout` / `network`：网络问题
- `decode`：应为 JSON 却返回 HTML 或坏 JSON
- `schema_mismatch`：站点结构变化
