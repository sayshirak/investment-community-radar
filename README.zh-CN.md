# 投资社区故事雷达（Investment Community Radar）

[English](README.md)

只读抓取 **Reddit** 个人投资社区、**V2EX** 指定节点与 **Hacker News**（`/front`、Ask HN、Show HN）中的高回复讨论，按规则筛出「热点新闻」与「长尾故事」，生成带原帖链接的中文材料清单。适合行为金融选题、公众号素材、跨社区投资故事检索。

**不做**跨站事件聚类，**不**自动贴心理学标签，也**不要求**同一话题同时出现在多个社区。

## 功能概览

- 三源并行：Reddit（8 个投资向 subreddit）、V2EX（投资/股票/比特币及扩展节点）、Hacker News（front / ask / show）
- 双轨筛选：近期热点 vs 长尾高回复故事
- Reddit 门槛按各板全时段评论数 **P95**（带 floor），带可配置 TTL 缓存
- Reddit / HN 可选免密英译中标题与摘要（可在配置中关闭）
- 输出结构化 JSON + Markdown；来源失败记入覆盖缺口，不用旧记忆硬补

## 环境要求

- Python 3.10+
- 能访问各公开数据源的网络环境

```bash
pip install -r requirements.txt
```

依赖：`requests`、`beautifulsoup4`、`lxml`。

## 快速开始

在仓库根目录执行：

```bash
python scripts/run_radar.py status
python scripts/run_radar.py collect
python scripts/run_radar.py all
```

| 命令 | 作用 |
| --- | --- |
| `status` | 探活各源（网络/代理有问题应先修好再 collect） |
| `collect` | 只写入 `output/latest/hotspots.json` |
| `report` | 基于最新 JSON 生成 Markdown（并尽量回填中文字段） |
| `all` | `collect` + 生成并打印 `weekly_hotspots.md` |

按平台过滤：

```bash
python scripts/run_radar.py collect --only reddit
python scripts/run_radar.py all --only v2ex
python scripts/run_radar.py all --only hackernews
python scripts/run_radar.py all --only reddit,hackernews
```

### 产出文件

- `output/latest/hotspots.json` — 结构化结果
- `output/latest/weekly_hotspots.md` — 中文阅读版（`all` / `report`）
- `output/reddit_percentiles.json` — Reddit P95 缓存
- `output/translation_cache.json` — 翻译缓存（仅内容哈希，无 Cookie）

## 数据来源

### Reddit

- `r/wallstreetbets`、`r/personalfinance`、`r/investing`、`r/Bogleheads`
- `r/financialindependence`、`r/ValueInvesting`、`r/stocks`、`r/dividends`

会排除标题以 `Daily FI discussion thread` 开头的日常汇总帖。优先走官方公开 JSON；整网 `403` 时降级到 **Arctic Shift** 公开归档 API（只读归档，不是代理或反爬绕过）。

### V2EX

主节点：`/go/invest`、`/go/stock`、`/go/bitcoin`。  
另扫程序员 / 商业 / 职场 / 生活等扩展节点与 `/recent`。会过滤开户广告、卖 AI 中转站 / API Key 等推广帖。

### Hacker News

- `/front`（按日回溯约一个月；外链会抓网页摘要写入 `summary`）
- Ask HN / Show HN（Algolia）

尽量排除纯科技/工程讨论：标题或摘要需带投资/决策信号才保留。

## 入选规则（摘要）

| 平台 | 热点新闻 | 长尾故事 |
| --- | --- | --- |
| Reddit | 近 **7** 个自然日，评论数 **>** `max(50, 该板 P95)` | 评论数 **>** `max(100, 该板 P95)`，不限日期 |
| V2EX | 近 **7** 日，回复 **>** 100 | 回复 **>** 1000，不限日期 |
| Hacker News | 近 **30** 日，回复 **>** 100 | 近 **30** 日，回复 **>** 1000 |

同时满足两条时归入长尾。阈值与时间窗可在 `config.json` 调整。

Reddit P95 按年分层抽样刷新，默认缓存 **24 小时**（`reddit.percentile_cache_ttl_hours`）。刷新失败则回退缓存，再不行只用 floor（50 / 100）。

## 配置

主要开关见 [`config.json`](config.json)：

- `request.*` — 超时、请求间隔、重试
- `rules.*` — 各平台回复门槛与回看窗口
- `reddit` / `v2ex` / `hackernews` — 板块、页数、过滤与归档参数
- `translation.enabled` — 是否生成中文题目/摘要

字段说明：[`references/fields.md`](references/fields.md)  
来源维护：[`references/sources.md`](references/sources.md)

## 目录结构

```
├── SKILL.md                 # Cursor Agent Skill 说明
├── config.json
├── requirements.txt
├── scripts/
│   ├── run_radar.py         # 命令行入口
│   ├── filtering.py
│   ├── translator.py
│   ├── report.py
│   └── sources/             # reddit / v2ex / hackernews 适配器
├── references/
├── tests/
└── output/                  # 本地生成（通常不提交）
```

## 测试

```bash
python -m unittest discover -s tests -v
```

## 安全边界

- 只读、低频、串行访问；不破验证码、不绕过封锁
- 不保存 Cookie、用户名列表或评论原文
- 报告只保留公开标题/摘要片段、计数、时间与链接
- 可选翻译会把 Reddit / HN 公开标题与摘要发到免密翻译端点；不需要时可在 `config.json` 关闭

## 许可证

仓库暂未附带 License 文件。若公开发布并希望他人合规复用，请自行补充合适的开源协议。

## 作为 Cursor Agent Skill

本仓库同时可作为 [Cursor](https://cursor.com) Agent Skill（见 `SKILL.md`）。放到 skills 目录后，在需要 Reddit / V2EX / HN 投资社区故事素材时调用即可。
