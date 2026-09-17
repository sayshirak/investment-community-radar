from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from filtering import (  # noqa: E402
    evaluate,
    is_hn_tech_discussion,
    is_v2ex_excluded_ad,
    recent_window_start,
    title_has_excluded_prefix,
)
from models import SourceHealth, Story  # noqa: E402
from reddit_percentiles import percentile_nearest_rank  # noqa: E402
from report import build_document, render_markdown  # noqa: E402
from run_radar import apply_saved_output_exclusions  # noqa: E402
from sources.hackernews import (  # noqa: E402
    parse_algolia_hits,
    parse_front_listing,
    summarize_external_html,
)
from sources.reddit import parse_archive_listing, parse_archive_posts  # noqa: E402
from sources.v2ex import parse_detail, parse_listing  # noqa: E402
from translator import RedditTranslator  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
# Local noon on 2026-08-02 makes the 7-day window start at 2026-07-27 00:00 local.
NOW = datetime(2026, 8, 2, 12, 0, 0).timestamp()
RULES = {
    "recent_post_days": 7,
    "v2ex": {
        "hot_replies_strictly_over": 100,
        "long_tail_replies_strictly_over": 1000,
    },
    "reddit": {
        "hot_replies_floor": 50,
        "long_tail_replies_floor": 100,
        "top_percentile": 95,
    },
    "hackernews": {
        "post_lookback_days": 30,
        "hot_replies_strictly_over": 100,
        "long_tail_replies_strictly_over": 1000,
    },
}
CFG = {"rules": RULES, "reddit_p95": {"test": 40}}


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()


def story(
    platform: str,
    replies: int,
    post_days: float,
    reply_hours: float = 1.0,
    community: str = "test",
) -> Story:
    return Story(
        platform=platform,
        community=community if platform != "reddit" else (
            community if community.startswith("r/") else f"r/{community}"
        ),
        title="Test investment story",
        summary="portfolio decision",
        url=f"https://example.com/{platform}/{replies}/{post_days}/{reply_hours}",
        posted_at=NOW - post_days * 86400,
        last_reply_at=NOW - reply_hours * 3600,
        reply_count=replies,
    )


class TestRules(unittest.TestCase):
    def test_daily_fi_thread_prefix_is_case_insensitive(self):
        prefixes = ["Daily FI discussion thread"]
        self.assertTrue(
            title_has_excluded_prefix(
                "Daily FI discussion thread - Monday, July 27, 2026",
                prefixes,
            )
        )
        self.assertTrue(
            title_has_excluded_prefix("daily fi DISCUSSION THREAD", prefixes)
        )
        self.assertFalse(title_has_excluded_prefix("My FIRE story", prefixes))

    def test_recent_window_matches_calendar_example(self):
        # 2026-07-07 local noon -> window starts 2026-07-01 00:00 local
        now = datetime(2026, 7, 7, 12, 0, 0).timestamp()
        start = recent_window_start(now, 7)
        self.assertEqual(
            datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S"),
            "2026-07-01 00:00:00",
        )

    def test_v2ex_hot_requires_over_100_within_7_days(self):
        too_few = story("v2ex", 100, 1)
        self.assertFalse(evaluate(too_few, CFG, NOW)[0])
        hot = story("v2ex", 101, 6)
        self.assertTrue(evaluate(hot, CFG, NOW)[0])
        self.assertEqual(hot.track, "recent")
        too_old = story("v2ex", 500, 8)
        self.assertFalse(evaluate(too_old, CFG, NOW)[0])

    def test_v2ex_long_tail_over_1000_ignores_post_date(self):
        candidate = story("v2ex", 1001, 400)
        accepted, reason = evaluate(candidate, CFG, NOW)
        self.assertTrue(accepted, reason)
        self.assertEqual(candidate.track, "long_tail")

    def test_reddit_hot_uses_max_floor_and_p95_within_7_days(self):
        # P95=40 for r/test => hot threshold max(50,40)=50
        too_few = story("reddit", 50, 1, community="test")
        self.assertFalse(evaluate(too_few, CFG, NOW)[0])
        hot = story("reddit", 51, 6, community="test")
        self.assertTrue(evaluate(hot, CFG, NOW)[0])
        self.assertEqual(hot.track, "recent")
        too_old = story("reddit", 80, 8, community="test")
        self.assertFalse(evaluate(too_old, CFG, NOW)[0])

        # Higher P95 binds instead of floor 50
        high_cfg = {
            "rules": RULES,
            "reddit_p95": {"wallstreetbets": 80},
        }
        miss = story("reddit", 80, 1, community="r/wallstreetbets")
        self.assertFalse(evaluate(miss, high_cfg, NOW)[0])
        hit = story("reddit", 81, 1, community="r/wallstreetbets")
        self.assertTrue(evaluate(hit, high_cfg, NOW)[0])
        self.assertEqual(hit.track, "recent")

    def test_reddit_long_tail_uses_max_floor_and_p95_ignores_post_date(self):
        # P95=40 => long threshold max(100,40)=100
        too_few = story("reddit", 100, 900, community="test")
        self.assertFalse(evaluate(too_few, CFG, NOW)[0])
        candidate = story("reddit", 101, 900, community="test")
        candidate.reply_count_is_lower_bound = True
        accepted, reason = evaluate(candidate, CFG, NOW)
        self.assertTrue(accepted, reason)
        self.assertEqual(candidate.track, "long_tail")
        self.assertIn("至少 101", candidate.reasons[0])

        high_cfg = {"rules": RULES, "reddit_p95": {"investing": 250}}
        miss = story("reddit", 250, 400, community="investing")
        self.assertFalse(evaluate(miss, high_cfg, NOW)[0])
        hit = story("reddit", 251, 400, community="investing")
        self.assertTrue(evaluate(hit, high_cfg, NOW)[0])
        self.assertEqual(hit.track, "long_tail")

    def test_long_tail_wins_when_both_match(self):
        candidate = story("reddit", 20000, 1)
        self.assertTrue(evaluate(candidate, CFG, NOW)[0])
        self.assertEqual(candidate.track, "long_tail")

    def test_percentile_nearest_rank_p95(self):
        values = list(range(1, 101))
        self.assertEqual(percentile_nearest_rank(values, 95), 95)
        self.assertEqual(percentile_nearest_rank([10], 95), 10)
        self.assertEqual(percentile_nearest_rank([], 95), 0)

    def test_v2ex_brokerage_and_ai_relay_ads_are_excluded(self):
        cfg = {
            "exclude_communities": ["promotions"],
            "exclude_brokerage_ad_terms": ["低佣开户", "免五", "大笑脸"],
            "exclude_ai_relay_ad_terms": ["中转站", "API中转", "成品号"],
        }
        self.assertTrue(
            is_v2ex_excluded_ad(
                "“万一免五”股票 ETF 大笑脸低佣开户",
                "欢迎来开！开户红包",
                "promotions",
                cfg,
            )
        )
        self.assertTrue(
            is_v2ex_excluded_ad(
                "直供 Claude 中转站，成品号月卡",
                "企业大厂来看看",
                "programmer",
                cfg,
            )
        )
        self.assertFalse(
            is_v2ex_excluded_ad(
                "老哥们，你们都是怎么开的 codex 的会员呢",
                "切美区苹果 id 买礼品卡充值的这种吗？",
                "programmer",
                cfg,
            )
        )

    def test_hn_hot_requires_over_100_within_30_days(self):
        too_few = story("hackernews", 100, 1)
        self.assertFalse(evaluate(too_few, CFG, NOW)[0])
        hot = story("hackernews", 101, 10)
        self.assertTrue(evaluate(hot, CFG, NOW)[0])
        self.assertEqual(hot.track, "recent")
        too_old = story("hackernews", 500, 40)
        self.assertFalse(evaluate(too_old, CFG, NOW)[0])

    def test_hn_long_tail_over_1000_still_requires_month_window(self):
        candidate = story("hackernews", 1001, 10)
        accepted, reason = evaluate(candidate, CFG, NOW)
        self.assertTrue(accepted, reason)
        self.assertEqual(candidate.track, "long_tail")
        too_old = story("hackernews", 5000, 40)
        self.assertFalse(evaluate(too_old, CFG, NOW)[0])

    def test_hn_tech_filter_keeps_invest_drops_pure_tech(self):
        self.assertFalse(
            is_hn_tech_discussion(
                "AI financial advice is surprisingly good",
                "portfolio decisions",
                {"_board": "front"},
            )
        )
        self.assertTrue(
            is_hn_tech_discussion(
                "Show HN: Elevators",
                "open source engine for inference",
                {"_board": "show"},
            )
        )
        self.assertTrue(
            is_hn_tech_discussion(
                "Ask HN: What are you working on?",
                "life update",
                {"_board": "ask"},
            )
        )
        self.assertTrue(
            is_hn_tech_discussion(
                "How to Exist",
                "philosophy essay",
                {"_board": "front"},
            )
        )
        # "ira" must not match inside "rather"
        self.assertTrue(
            is_hn_tech_discussion(
                "Show HN: Echo",
                "rather than choosing a single model on the same evaluations",
                {"_board": "show"},
            )
        )
        self.assertFalse(
            is_hn_tech_discussion(
                "Ask HN: Best way to handle a sudden job layoff?",
                "I need a financial plan",
                {"_board": "ask"},
            )
        )


class TestV2EXParsing(unittest.TestCase):
    def test_recent_listing_keeps_actual_node_and_priority(self):
        rows = parse_listing(
            fixture("v2ex_listing.html"),
            "https://www.v2ex.com",
            "recent",
            {"invest", "stock", "bitcoin"},
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["community"], "invest")
        self.assertEqual(rows[0]["reply_count"], 120)
        self.assertTrue(rows[0]["source_priority"])
        self.assertEqual(rows[0]["url"], "https://www.v2ex.com/t/100")

    def test_detail_reads_exact_times_and_omits_comments(self):
        listing = parse_listing(
            fixture("v2ex_listing.html"),
            "https://www.v2ex.com",
            "recent",
            {"invest"},
        )[0]
        parsed = parse_detail(fixture("v2ex_detail.html"), listing)
        self.assertEqual(parsed.reply_count, 120)
        self.assertIn("账户跌了很多", parsed.summary)
        self.assertNotIn("评论正文", parsed.summary)
        self.assertGreater(parsed.last_reply_at, parsed.posted_at)


class TestRedditParsing(unittest.TestCase):
    def test_archive_posts_and_listing(self):
        posts = parse_archive_posts(json.loads(fixture("reddit_posts.json")), "investing")
        self.assertIn("abc123", posts)
        self.assertEqual(posts["abc123"].community, "r/investing")
        listing = parse_archive_listing(json.loads(fixture("reddit_posts.json")), "investing")
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0].title.startswith("I lost"), True)

    def test_wrong_archive_shape_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "schema_mismatch"):
            parse_archive_posts({"data": {}}, "stocks")


class TestHackerNewsParsing(unittest.TestCase):
    def test_front_listing_and_external_summary(self):
        rows = parse_front_listing(fixture("hn_front.html"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["reply_count"], 233)
        self.assertTrue(rows[0]["external_url"].startswith("https://"))
        self.assertEqual(rows[1]["url"], "https://news.ycombinator.com/item?id=1001")
        summary = summarize_external_html(fixture("hn_external.html"))
        self.assertIn("financial advice", summary.casefold())
        self.assertIn("portfolio decisions", summary.casefold())

    def test_algolia_ask_hits(self):
        rows = parse_algolia_hits(json.loads(fixture("hn_algolia_ask.json")), "ask")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["board"], "ask")
        self.assertEqual(rows[0]["reply_count"], 256)
        self.assertIn("financial plan", rows[0]["summary"])


class TestReport(unittest.TestCase):
    def test_report_has_no_comment_text(self):
        candidate = story("v2ex", 101, 1)
        candidate.title = "亏损故事"
        candidate.summary = "主帖摘要"
        self.assertTrue(evaluate(candidate, CFG, NOW)[0])
        document = build_document(
            [candidate],
            [SourceHealth("v2ex", True, 1, 1)],
            NOW,
        )
        markdown = render_markdown(document)
        payload = json.dumps(document, ensure_ascii=False)
        self.assertIn("亏损故事", markdown)
        self.assertIn("主帖摘要", payload)
        self.assertNotIn("comment text is transient", payload)
        self.assertIn("热点新闻", markdown)

    def test_saved_output_exclusion_removes_daily_fi_threads(self):
        document = {
            "stories": {
                "reddit": {
                    "long_tail": [],
                    "recent": [
                        {"title": "Daily FI discussion thread - Monday"},
                        {"title": "My retirement story"},
                    ],
                },
                "v2ex": {
                    "long_tail": [],
                    "recent": [
                        {
                            "title": "万一免五低佣开户",
                            "summary": "开户红包",
                            "community": "promotions",
                        },
                        {
                            "title": "存了 100w 想躺平",
                            "summary": "无房无贷",
                            "community": "life",
                        },
                    ],
                },
                "hackernews": {
                    "long_tail": [],
                    "recent": [
                        {
                            "title": "Show HN: Elevators",
                            "summary": "open source inference engine",
                            "community": "show",
                        },
                        {
                            "title": "AI financial advice is surprisingly good",
                            "summary": "portfolio decisions",
                            "community": "front",
                        },
                    ],
                },
            }
        }
        apply_saved_output_exclusions(
            document,
            {
                "reddit": {"exclude_title_prefixes": ["Daily FI discussion thread"]},
                "v2ex": {
                    "exclude_communities": ["promotions"],
                    "exclude_brokerage_ad_terms": ["低佣开户", "免五"],
                    "exclude_ai_relay_ad_terms": ["中转站"],
                },
                "hackernews": {},
            },
        )
        self.assertEqual(
            [row["title"] for row in document["stories"]["reddit"]["recent"]],
            ["My retirement story"],
        )
        self.assertEqual(
            [row["title"] for row in document["stories"]["v2ex"]["recent"]],
            ["存了 100w 想躺平"],
        )
        self.assertEqual(
            [row["title"] for row in document["stories"]["hackernews"]["recent"]],
            ["AI financial advice is surprisingly good"],
        )

    def test_reddit_chinese_fields_are_serialized_and_rendered(self):
        candidate = story("reddit", 80, 1)
        candidate.title_zh = "中文题目"
        candidate.summary_zh = "中文摘要"
        self.assertTrue(evaluate(candidate, CFG, NOW)[0])
        self.assertEqual(candidate.track, "recent")
        document = build_document(
            [candidate],
            [SourceHealth("reddit", True, 1, 1)],
            NOW,
        )
        row = document["stories"]["reddit"]["recent"][0]
        self.assertEqual(row["title_zh"], "中文题目")
        self.assertEqual(row["summary_zh"], "中文摘要")
        markdown = render_markdown(document)
        self.assertIn("[中文题目]", markdown)
        self.assertIn("中文摘要：中文摘要", markdown)


class TestTranslation(unittest.TestCase):
    def test_translator_splits_pair_and_caches(self):
        class FakeClient:
            def get_json(self, *_args, **_kwargs):
                return [[
                    ["中文题目\n__RADAR_SPLIT_9F4A__\n中文摘要", "", None, None]
                ]]

        candidate = story("reddit", 1001, 1)
        candidate.title = "English title"
        candidate.summary = "English summary"
        with tempfile.TemporaryDirectory() as directory:
            translator = RedditTranslator(
                {"enabled": True, "cache_file": "translations.json"},
                FakeClient(),
                directory,
            )
            translator.translate_all([candidate])
            self.assertEqual(candidate.title_zh, "中文题目")
            self.assertEqual(candidate.summary_zh, "中文摘要")
            self.assertTrue(os.path.exists(os.path.join(directory, "translations.json")))

    def test_translator_backfills_saved_document(self):
        class FakeClient:
            def get_json(self, *_args, **_kwargs):
                return [[["已翻译\n__RADAR_SPLIT_9F4A__\n已翻译摘要", "", None, None]]]

        document = {
            "stories": {
                "reddit": {
                    "long_tail": [],
                    "recent": [{"title": "Title", "summary": "Summary"}],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            translator = RedditTranslator(
                {"enabled": True, "cache_file": "translations.json"},
                FakeClient(),
                directory,
            )
            translator.translate_document(document)
        row = document["stories"]["reddit"]["recent"][0]
        self.assertEqual(row["title_zh"], "已翻译")
        self.assertEqual(row["summary_zh"], "已翻译摘要")


if __name__ == "__main__":
    unittest.main()
