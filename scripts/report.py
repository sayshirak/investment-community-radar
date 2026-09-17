"""JSON document assembly and Markdown rendering."""

from __future__ import annotations

from typing import Any

from models import SourceHealth, Story, format_ts


def build_document(
    stories: list[Story],
    health: list[SourceHealth],
    generated_at: float,
    cfg: dict[str, Any] | None = None,
    reddit_percentiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections: dict[str, dict[str, list[dict[str, Any]]]] = {
        "reddit": {"long_tail": [], "recent": []},
        "v2ex": {"long_tail": [], "recent": []},
        "hackernews": {"long_tail": [], "recent": []},
    }
    for story in stories:
        sections[story.platform][story.track].append(story.to_dict())

    rules_cfg = (cfg or {}).get("rules", {})
    reddit_rules = rules_cfg.get("reddit", {})
    document: dict[str, Any] = {
        "generated_at": format_ts(generated_at),
        "rules": {
            "recent_post_days": int(rules_cfg.get("recent_post_days", 7)),
            "reddit": {
                "hot_replies_floor": int(reddit_rules.get("hot_replies_floor", 50)),
                "long_tail_replies_floor": int(
                    reddit_rules.get("long_tail_replies_floor", 100)
                ),
                "top_percentile": int(reddit_rules.get("top_percentile", 95)),
                "threshold": "comments > max(floor, per-subreddit all-time P95)",
            },
            "v2ex": {
                "hot_replies_strictly_over": 100,
                "long_tail_replies_strictly_over": 1000,
            },
            "hackernews": {
                "post_lookback_days": 30,
                "hot_replies_strictly_over": 100,
                "long_tail_replies_strictly_over": 1000,
            },
        },
        "sources": [item.to_dict() for item in health],
        "stories": sections,
    }
    if reddit_percentiles:
        document["reddit_percentiles"] = {
            "generated_at": reddit_percentiles.get("generated_at"),
            "method": reddit_percentiles.get("method"),
            "percentile": reddit_percentiles.get("percentile"),
            "subreddits": reddit_percentiles.get("subreddits"),
        }
    return document


def render_markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Reddit + V2EX + Hacker News 故事雷达",
        "",
        f"数据截止：{document['generated_at']}",
        "",
    ]
    failed = [item for item in document["sources"] if not item["ok"]]
    if failed:
        lines.extend(["> 覆盖缺口："])
        for item in failed:
            lines.append(f"> - {item['name']}：{item['reason']}")
        lines.append("")

    percentiles = document.get("reddit_percentiles") or {}
    sub_p95 = percentiles.get("subreddits") or {}
    if sub_p95:
        lines.append("## Reddit 分位门槛（本次刷新）")
        lines.append("")
        for name, row in sub_p95.items():
            if not isinstance(row, dict):
                continue
            p95 = row.get("p95", 0)
            lines.append(
                f"- r/{name}：P95={p95} → 热点 >{max(50, int(p95))}，"
                f"长尾 >{max(100, int(p95))}（样本 {row.get('sample_size', 0)}）"
            )
        lines.append("")

    for platform, platform_label in (
        ("reddit", "Reddit"),
        ("v2ex", "V2EX"),
        ("hackernews", "Hacker News"),
    ):
        platform_stories = document["stories"].get(platform)
        if platform_stories is None:
            continue
        lines.append(f"## {platform_label}")
        lines.append("")
        for track, track_label in (("long_tail", "长尾故事"), ("recent", "热点新闻")):
            rows = platform_stories.get(track, [])
            lines.append(f"### {track_label}（{len(rows)}）")
            lines.append("")
            if not rows:
                lines.append("（本次没有满足入选规则的帖子。）")
                lines.append("")
                continue
            for index, row in enumerate(rows, 1):
                priority = " · 主节点" if row.get("source_priority") and platform == "v2ex" else ""
                display_title = row.get("title_zh") or row["title"]
                reply_count = (
                    f"≥{row['reply_count']}"
                    if row.get("reply_count_is_lower_bound")
                    else str(row["reply_count"])
                )
                lines.extend(
                    [
                        f"#### {index}. [{display_title}]({row['url']})",
                        "",
                        (
                            f"- 来源：{row['community']}{priority}｜材料：{row['material_type']}"
                            f"｜回复：{reply_count}"
                        ),
                        f"- 发帖：{row['posted_at']}｜最后回复：{row['last_reply_at']}",
                        f"- 入选：{'；'.join(row['reasons'])}",
                    ]
                )
                if platform in ("reddit", "hackernews") and row.get("title_zh"):
                    lines.append(f"- 原题：{row['title']}")
                if platform in ("reddit", "hackernews") and row.get("summary_zh"):
                    lines.append(f"- 中文摘要：{row['summary_zh']}")
                if row.get("summary"):
                    label = (
                        "原文摘要"
                        if platform in ("reddit", "hackernews") and row.get("summary_zh")
                        else "摘要"
                    )
                    lines.append(f"- {label}：{row['summary']}")
                lines.append("")

    lines.extend(
        [
            "---",
            "",
            "规则：",
            "- Reddit 热点新闻：近 7 个自然日内发帖，且评论数 > max(50, 该板全时段 P95)",
            "- Reddit 长尾故事：评论数 > max(100, 该板全时段 P95)，不限发帖日期",
            "- V2EX 热点新闻：近 7 个自然日内发帖，且回复数 > 100",
            "- V2EX 长尾故事：回复数 > 1000，不限发帖日期",
            "- Hacker News 热点新闻：近 30 个自然日内发帖，且回复数 > 100",
            "- Hacker News 长尾故事：近 30 个自然日内发帖，且回复数 > 1000",
            "材料分类只用于阅读，不参与入选。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
