# 字段与筛选口径

## Story

- `platform`：`reddit`、`v2ex` 或 `hackernews`
- `community`：subreddit、V2EX 节点名，或 HN 板块（`front` / `ask` / `show`）
- `title`：主帖标题
- `summary`：主帖正文公开摘要，最多 500 字符；HN `/front` 外链为网页摘要
- `title_zh`：Reddit / Hacker News 中文题目；翻译失败为空
- `summary_zh`：Reddit / Hacker News 中文摘要；原摘要为空或翻译失败时为空
- `url`：原帖链接（HN 为 `item?id=` 讨论页）
- `posted_at`：发帖时间
- `last_reply_at`：最新回复时间（展示用，不参与入选）
- `reply_count`：Reddit 评论数、V2EX 回复数或 HN 评论数
- `reply_count_is_lower_bound`：计数达到探测上限时为 true；报告显示为 `≥N`
- `source_priority`：是否来自 V2EX 三个主节点；Reddit 固定为 true；HN 的 `front`/`ask` 为 true
- `material_type`：非门槛式阅读标签
- `track`：`recent`（热点新闻）或 `long_tail`（长尾故事）
- `reasons`：入选规则命中说明

不保存评论正文、评论者、主帖作者或 Cookie。

## 规则

```text
window_start_7 = local midnight of (today - 6 days)
# example: today=2026-07-07 => window_start=2026-07-01 00:00:00

window_start_30 = local midnight of (today - 29 days)

Reddit:
  p95       = all-time top 5% comment count estimated per subreddit
  long_tail = reply_count > max(100, p95)
  hot       = posted_at >= window_start_7 AND reply_count > max(50, p95)

V2EX:
  long_tail = reply_count > 1000
  hot       = posted_at >= window_start_7 AND reply_count > 100

Hacker News:
  long_tail = posted_at >= window_start_30 AND reply_count > 1000
  hot       = posted_at >= window_start_30 AND reply_count > 100

if long_tail: track = long_tail
else if hot: track = recent
else: reject
```

`material_type` 不参与入选。同一话题无需跨节点或跨平台出现。
