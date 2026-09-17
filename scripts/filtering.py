"""Admission rules and non-gating classification."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from models import Story


def title_has_excluded_prefix(title: str, prefixes: list[str]) -> bool:
    normalized = (title or "").strip().casefold()
    return any(
        normalized.startswith(prefix.strip().casefold())
        for prefix in prefixes
        if prefix.strip()
    )


def _normalize_ad_text(text: str) -> str:
    return (
        (text or "")
        .casefold()
        .replace(" ", "")
        .replace("　", "")
        .replace("\n", "")
        .replace("\t", "")
    )


def is_v2ex_excluded_ad(
    title: str,
    summary: str = "",
    community: str = "",
    cfg: dict[str, Any] | None = None,
) -> bool:
    """Drop brokerage account-opening promos and AI API relay sales posts."""
    cfg = cfg or {}
    text = _normalize_ad_text(f"{title}\n{summary}")
    node = (community or "").strip().casefold()

    excluded_nodes = {
        str(item).strip().casefold()
        for item in cfg.get("exclude_communities", [])
        if str(item).strip()
    }
    if node and node in excluded_nodes:
        return True

    brokerage_terms = [
        _normalize_ad_text(item)
        for item in cfg.get(
            "exclude_brokerage_ad_terms",
            [
                "低佣开户",
                "免五",
                "免5",
                "开户推荐",
                "开户抽奖",
                "开户红包",
                "券商开户",
                "股票开户",
                "etf开户",
                "大笑脸",
            ],
        )
        if str(item).strip()
    ]
    if any(term and term in text for term in brokerage_terms):
        return True

    relay_terms = [
        _normalize_ad_text(item)
        for item in cfg.get(
            "exclude_ai_relay_ad_terms",
            [
                "中转站",
                "api中转",
                "中转api",
                "openai中转",
                "claude中转",
                "gpt中转",
                "gemini中转",
                "直供claude",
                "直供gpt",
                "直供openai",
                "官网key",
                "成品号",
                "特殊渠道独享",
                "企业大厂来看看",
                "最便宜的token",
                "token中转",
            ],
        )
        if str(item).strip()
    ]
    if any(term and term in text for term in relay_terms):
        return True

    return False


def _term_matches(term: str, text: str) -> bool:
    """Match whole tokens/phrases; avoid substrings like ira⊂rather, valuation⊂evaluations."""
    needle = (term or "").strip().casefold()
    if not needle:
        return False
    if " " in needle:
        return needle in text
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            text,
        )
        is not None
    )


def is_hn_tech_discussion(
    title: str,
    summary: str = "",
    cfg: dict[str, Any] | None = None,
) -> bool:
    """Return True when a HN item should be dropped.

    Hacker News is noisy with engineering threads. Keep only posts whose
    title/summary carry investment or decision signals. Strong tech terms
    without those signals are also dropped.
    """
    cfg = cfg or {}
    text = f"{title}\n{summary}".casefold()

    invest_terms = [
        str(item).strip().casefold()
        for item in cfg.get(
            "invest_decision_terms",
            [
                "invest",
                "investment",
                "investor",
                "stock",
                "market",
                "portfolio",
                "retire",
                "retirement",
                "pension",
                "401k",
                "ira",
                "salary",
                "wage",
                "layoff",
                "laid off",
                "unemploy",
                "finance",
                "financial",
                "money",
                "wealth",
                "income",
                "tax",
                "debt",
                "mortgage",
                "housing",
                "rent",
                "cost of living",
                "inflation",
                "bank",
                "banking",
                "credit",
                "insurance",
                "trading",
                "trader",
                "crypto",
                "bitcoin",
                " eth",
                "valuation",
                "funding",
                "ipo",
                "earnings",
                "recession",
                "budget",
                "saving",
                "savings",
                "career",
                "job offer",
                "compensation",
                "decision",
                "risk",
                "behavioral",
                "psychology",
                "bias",
                "billing",
            ],
        )
        if str(item).strip()
    ]
    if any(_term_matches(term, text) for term in invest_terms):
        return False

    tech_terms = [
        str(item).strip().casefold()
        for item in cfg.get(
            "exclude_tech_terms",
            [
                "kubernetes",
                "docker",
                "typescript",
                "javascript",
                "golang",
                "rust ",
                "llvm",
                "compiler",
                "linux kernel",
                "open-source",
                "open source",
                "github",
                "gitlab",
                "npm ",
                "wasm",
                "webassembly",
                "postgresql",
                "sqlite",
                "mongodb",
                "redis",
                "graphql",
                "rest api",
                "sdk",
                "cli ",
                "devtools",
                "framework",
                "programming language",
                "source code",
                "pull request",
                "show hn:",
                "vscode",
                "emacs",
                "neovim",
                "tailscale",
                "cloudflare",
                "aws ",
                "gcp ",
                "azure",
                "cuda",
                "gpu ",
                "llm",
                "transformer",
                "inference",
                "fine-tun",
                "rag ",
                "vector database",
                "css ",
                "html ",
                "react ",
                "vue ",
                "svelte",
                "next.js",
                "flutter",
                "android",
                "ios app",
                "esp32",
                "raspberry pi",
                "kernel",
                "debugger",
                "benchmark",
                "latency",
                "throughput",
            ],
        )
        if str(item).strip()
    ]
    if any(_term_matches(term, text) for term in tech_terms):
        return True

    # No invest/decision signal -> out of scope for this radar.
    return True


def recent_window_start(now: float, days: int = 7) -> float:
    """Inclusive local calendar window.

    Example: if today is 2026-07-07 and days=7, start is 2026-07-01 00:00:00 local.
    """
    local = datetime.fromtimestamp(now).astimezone()
    start_day = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return start_day.timestamp()


def classify_material(story: Story) -> str:
    """Assign a reading aid. Classification never controls admission."""
    text = f"{story.title} {story.summary}".lower()
    groups = [
        (
            "极端投机",
            (
                "梭哈", "爆仓", "杠杆", "期权", "合约", "暴富", "亏麻",
                "all in", "yolo", "loss", "gain porn", "margin", "options",
            ),
        ),
        (
            "长期投资",
            (
                "定投", "指数", "etf", "分红", "退休", "财务自由", "fire",
                "bogle", "dividend", "index fund", "retirement",
            ),
        ),
        (
            "个人财务",
            (
                "负债", "房贷", "存款", "工资", "失业", "应急金", "预算",
                "debt", "mortgage", "salary", "emergency fund", "budget",
            ),
        ),
        (
            "投资决策",
            (
                "加仓", "割肉", "抄底", "仓位", "持仓", "买入", "卖出",
                "buy", "sell", "portfolio", "position", "dip",
            ),
        ),
        (
            "职业与创业",
            (
                "创业", "副业", "裁员", "跳槽", "职场", "生意",
                "startup", "business", "career", "layoff", "job",
            ),
        ),
        (
            "生活剧本",
            ("家庭", "婚姻", "父母", "孩子", "人生", "生活", "family", "life"),
        ),
    ]
    for label, words in groups:
        if any(word in text for word in words):
            return label
    return "社区热点"


def evaluate(story: Story, cfg: dict[str, Any], now: float) -> tuple[bool, str]:
    """Apply platform-specific hot / long-tail gates.

    Reddit:
      - per-subreddit all-time P95 from cfg["reddit_p95"]
      - hot: posted within recent calendar window AND replies > max(hot_floor, P95)
      - long_tail: replies > max(long_floor, P95) (no post-date limit)

    V2EX:
      - hot: posted within recent calendar window AND replies > hot_replies
      - long_tail: replies > long_tail_replies (no post-date limit)

    Hacker News:
      - all tracks require posted within post_lookback_days (default 30)
      - hot: replies > hot_replies_strictly_over (default 100)
      - long_tail: replies > long_tail_replies_strictly_over (default 1000)

    If both match, long_tail wins.
    """
    rules = cfg["rules"]
    if story.platform == "reddit":
        return _evaluate_reddit(story, cfg, now)
    if story.platform == "hackernews":
        return _evaluate_hackernews(story, rules, now)

    platform_rules = rules[story.platform]
    hot_min = int(platform_rules["hot_replies_strictly_over"])
    long_min = int(platform_rules["long_tail_replies_strictly_over"])
    window_days = int(rules["recent_post_days"])

    if story.reply_count < 0:
        return False, "回复数无效"
    if story.posted_at > now + 3600:
        return False, "发帖时间异常（晚于采集时间）"

    count_text = (
        f"至少 {story.reply_count}"
        if story.reply_count_is_lower_bound
        else str(story.reply_count)
    )

    if story.reply_count > long_min:
        story.track = "long_tail"
        story.material_type = classify_material(story)
        story.reasons = [
            f"{story.platform} 回复数 {count_text}，严格超过长尾门槛 {long_min}",
            "长尾故事不限制发帖日期",
        ]
        return True, ""

    if not story.posted_at:
        return False, "缺少发帖时间（热点新闻需要发帖日期）"

    window_start = recent_window_start(now, window_days)
    if story.posted_at < window_start:
        return False, f"发帖时间早于近 {window_days} 天窗口，且未达到长尾回复门槛"
    if story.reply_count > hot_min:
        story.track = "recent"
        story.material_type = classify_material(story)
        story.reasons = [
            f"发帖时间在近 {window_days} 天内（含首尾自然日）",
            f"{story.platform} 回复数 {count_text}，严格超过热点门槛 {hot_min}",
        ]
        return True, ""

    return False, f"回复数未超过热点门槛 {hot_min}，也未超过长尾门槛 {long_min}"


def reddit_thresholds(
    story: Story,
    cfg: dict[str, Any],
) -> tuple[int, int, int]:
    """Return (p95, hot_min, long_min) for a Reddit story."""
    rules = cfg["rules"]["reddit"]
    floor_hot = int(rules.get("hot_replies_floor", 50))
    floor_long = int(rules.get("long_tail_replies_floor", 100))
    sub = (story.community or "").removeprefix("r/").strip()
    p95_map = cfg.get("reddit_p95") or rules.get("p95_by_subreddit") or {}
    p95 = int(p95_map.get(sub, 0) or 0)
    return p95, max(floor_hot, p95), max(floor_long, p95)


def _evaluate_reddit(story: Story, cfg: dict[str, Any], now: float) -> tuple[bool, str]:
    rules = cfg["rules"]
    reddit_rules = rules["reddit"]
    window_days = int(rules["recent_post_days"])
    floor_hot = int(reddit_rules.get("hot_replies_floor", 50))
    floor_long = int(reddit_rules.get("long_tail_replies_floor", 100))
    p95, hot_min, long_min = reddit_thresholds(story, cfg)

    if story.reply_count < 0:
        return False, "回复数无效"
    if story.posted_at > now + 3600:
        return False, "发帖时间异常（晚于采集时间）"

    count_text = (
        f"至少 {story.reply_count}"
        if story.reply_count_is_lower_bound
        else str(story.reply_count)
    )
    threshold_note = (
        f"P95={p95}，热点门槛 max({floor_hot},{p95})={hot_min}，"
        f"长尾门槛 max({floor_long},{p95})={long_min}"
    )

    if story.reply_count > long_min:
        story.track = "long_tail"
        story.material_type = classify_material(story)
        story.reasons = [
            f"reddit 回复数 {count_text}，严格超过长尾门槛 {long_min}（{threshold_note}）",
            "长尾故事不限制发帖日期",
        ]
        return True, ""

    if not story.posted_at:
        return False, "缺少发帖时间（热点新闻需要发帖日期）"

    window_start = recent_window_start(now, window_days)
    if story.posted_at < window_start:
        return False, f"发帖时间早于近 {window_days} 天窗口，且未达到长尾回复门槛"
    if story.reply_count > hot_min:
        story.track = "recent"
        story.material_type = classify_material(story)
        story.reasons = [
            f"发帖时间在近 {window_days} 天内（含首尾自然日）",
            f"reddit 回复数 {count_text}，严格超过热点门槛 {hot_min}（{threshold_note}）",
        ]
        return True, ""

    return False, (
        f"回复数未超过热点门槛 {hot_min}，也未超过长尾门槛 {long_min}（{threshold_note}）"
    )


def _evaluate_hackernews(story: Story, rules: dict[str, Any], now: float) -> tuple[bool, str]:
    platform_rules = rules["hackernews"]
    lookback_days = int(platform_rules.get("post_lookback_days", 30))
    hot_min = int(
        platform_rules.get(
            "hot_replies_strictly_over",
            platform_rules.get("hot_replies_at_least", 100),
        )
    )
    long_min = int(platform_rules.get("long_tail_replies_strictly_over", 1000))

    if story.reply_count < 0:
        return False, "回复数无效"
    if story.posted_at > now + 3600:
        return False, "发帖时间异常（晚于采集时间）"
    if not story.posted_at:
        return False, "缺少发帖时间（Hacker News 需要近一个月发帖窗口）"

    window_start = recent_window_start(now, lookback_days)
    if story.posted_at < window_start:
        return False, f"发帖时间早于近 {lookback_days} 天窗口"

    count_text = (
        f"至少 {story.reply_count}"
        if story.reply_count_is_lower_bound
        else str(story.reply_count)
    )

    if story.reply_count > long_min:
        story.track = "long_tail"
        story.material_type = classify_material(story)
        story.reasons = [
            f"发帖时间在近 {lookback_days} 天内（含首尾自然日）",
            f"hackernews 回复数 {count_text}，严格超过长尾门槛 {long_min}",
        ]
        return True, ""

    if story.reply_count > hot_min:
        story.track = "recent"
        story.material_type = classify_material(story)
        story.reasons = [
            f"发帖时间在近 {lookback_days} 天内（含首尾自然日）",
            f"hackernews 回复数 {count_text}，严格超过热点门槛 {hot_min}",
        ]
        return True, ""

    return False, f"回复数未超过热点门槛 {hot_min}，也未超过长尾门槛 {long_min}"
