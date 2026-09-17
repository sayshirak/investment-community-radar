"""Reddit adapter with official JSON and Arctic Shift fallback."""

from __future__ import annotations

import os
from typing import Any

from config import SKILL_ROOT, output_dir
from filtering import evaluate, recent_window_start, reddit_thresholds, title_has_excluded_prefix
from http_client import FetchError
from models import SourceHealth, Story
from reddit_percentiles import (
    load_cached_percentiles,
    p95_map_from_document,
    refresh_reddit_percentiles,
)
from sources.base import SourceAdapter


def _clean_post_id(value: str) -> str:
    return (value or "").removeprefix("t3_")


def parse_official_listing(data: Any, subreddit: str) -> list[Story]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ValueError("schema_mismatch: Reddit listing data missing")
    children = data["data"].get("children")
    if not isinstance(children, list):
        raise ValueError("schema_mismatch: Reddit listing children missing")
    stories: list[Story] = []
    for child in children:
        post = child.get("data", {}) if isinstance(child, dict) else {}
        if post.get("stickied") or not post.get("title"):
            continue
        stories.append(story_from_post(post, subreddit))
    return stories


def parse_archive_posts(data: Any, subreddit: str) -> dict[str, Story]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("schema_mismatch: archive post list missing")
    out: dict[str, Story] = {}
    for post in data["data"]:
        if not isinstance(post, dict) or post.get("stickied") or not post.get("title"):
            continue
        post_id = _clean_post_id(str(post.get("id") or post.get("name") or ""))
        if post_id:
            out[post_id] = story_from_post(post, subreddit)
    return out


def parse_archive_listing(data: Any, subreddit: str) -> list[Story]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("schema_mismatch: archive post list missing")
    stories: list[Story] = []
    for post in data["data"]:
        if not isinstance(post, dict) or post.get("stickied") or not post.get("title"):
            continue
        stories.append(story_from_post(post, subreddit))
    return stories


def story_from_post(post: dict[str, Any], subreddit: str) -> Story:
    permalink = str(post.get("permalink") or "")
    if permalink.startswith("/"):
        url = f"https://www.reddit.com{permalink}"
    else:
        url = permalink or str(post.get("url") or "")
    summary = str(post.get("selftext") or "").strip()
    if summary in ("[removed]", "[deleted]"):
        summary = ""
    posted = float(post.get("created_utc") or post.get("created") or 0)
    return Story(
        platform="reddit",
        community=f"r/{subreddit}",
        title=str(post.get("title") or "").strip(),
        summary=summary.replace("\n", " ")[:500],
        url=url,
        posted_at=posted,
        last_reply_at=posted,
        reply_count=int(post.get("num_comments") or 0),
        source_priority=True,
    )


class RedditSource(SourceAdapter):
    name = "reddit"

    def __init__(self, cfg: dict[str, Any], client: Any):
        super().__init__(cfg, client)
        self.last_percentiles: dict[str, Any] | None = None
        self._p95_map: dict[str, int] = {}

    def collect(self, now: float) -> tuple[list[Story], SourceHealth]:
        subreddits = list(self.cfg.get("subreddits", []))
        percentile_note = self._prepare_percentiles()
        try:
            base = self._probe_official_base(subreddits[0])
        except Exception as official_error:  # noqa: BLE001
            try:
                stories, seen, failures = self._collect_archive(subreddits, now)
            except Exception as archive_error:  # noqa: BLE001
                return [], SourceHealth(
                    self.name,
                    False,
                    0,
                    0,
                    f"official unavailable: {official_error}; archive failed: {archive_error}",
                )
            reason = "via Arctic Shift archive (Reddit official JSON unavailable)"
            if failures:
                reason += f"; partial subreddit failures={len(failures)}"
            if percentile_note:
                reason = f"{reason}; {percentile_note}" if reason else percentile_note
            return stories, SourceHealth(self.name, True, seen, len(stories), reason)

        stories, seen, failures = self._collect_official(subreddits, now, base)
        reason = f"partial subreddit failures={len(failures)}" if failures else ""
        if percentile_note:
            reason = f"{reason}; {percentile_note}" if reason else percentile_note
        return stories, SourceHealth(
            self.name, bool(stories or not failures), seen, len(stories), reason
        )

    def probe(self) -> SourceHealth:
        sub = self.cfg.get("subreddits", ["investing"])[0]
        try:
            base = self._probe_official_base(sub)
            return SourceHealth(self.name, True, reason=f"official JSON via {base}")
        except Exception as official_error:  # noqa: BLE001
            try:
                self.client.get_json(
                    f"{self._archive_base()}/api/posts/search",
                    params={"subreddit": sub, "limit": 1, "sort": "desc"},
                )
                return SourceHealth(
                    self.name,
                    True,
                    reason=f"Arctic Shift fallback available; official: {official_error}",
                )
            except Exception as archive_error:  # noqa: BLE001
                return SourceHealth(
                    self.name,
                    False,
                    reason=f"official: {official_error}; archive: {archive_error}",
                )

    def _probe_official_base(self, subreddit: str) -> str:
        errors: list[str] = []
        for base in self.cfg.get("official_bases", []):
            path = (
                f"/r/{subreddit}/hot"
                if "api.reddit.com" in base
                else f"/r/{subreddit}/hot.json"
            )
            try:
                data = self.client.get_json(
                    f"{base.rstrip('/')}{path}",
                    params={"limit": 25, "raw_json": 1},
                    headers={"Accept": "application/json"},
                )
                parse_official_listing(data, subreddit)
                return base.rstrip("/")
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        raise FetchError("official_unavailable", " | ".join(errors))

    def _collect_official(
        self,
        subreddits: list[str],
        now: float,
        base: str,
    ) -> tuple[list[Story], int, list[str]]:
        selected: dict[str, Story] = {}
        failures: list[str] = []
        seen = 0
        eval_cfg = self._eval_cfg()
        total = len(subreddits)
        for index, sub in enumerate(subreddits, start=1):
            print(f"[reddit] official {index}/{total} r/{sub}", flush=True)
            try:
                for path, params in self._official_listing_specs(base, sub):
                    data = self.client.get_json(
                        f"{base}{path}",
                        params={**params, "raw_json": 1},
                        headers={"Accept": "application/json"},
                    )
                    for story in parse_official_listing(data, sub):
                        if title_has_excluded_prefix(
                            story.title,
                            self.cfg.get("exclude_title_prefixes", []),
                        ):
                            continue
                        _, hot_min, _ = reddit_thresholds(story, eval_cfg)
                        if story.reply_count <= hot_min:
                            continue
                        seen += 1
                        accepted, _ = evaluate(story, eval_cfg, now)
                        if accepted:
                            previous = selected.get(story.url)
                            if not previous or story.reply_count > previous.reply_count:
                                selected[story.url] = story
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{sub}:{exc}")
        return self._sort(list(selected.values())), seen, failures

    def _official_listing_specs(
        self, base: str, subreddit: str
    ) -> list[tuple[str, dict[str, Any]]]:
        if "api.reddit.com" in base:
            return [
                (f"/r/{subreddit}/new", {"limit": 100}),
                (f"/r/{subreddit}/top", {"limit": 100, "t": "week"}),
                (f"/r/{subreddit}/top", {"limit": 100, "t": "year"}),
                (f"/r/{subreddit}/top", {"limit": 100, "t": "all"}),
                (f"/r/{subreddit}/hot", {"limit": 100}),
            ]
        return [
            (f"/r/{subreddit}/new.json", {"limit": 100}),
            (f"/r/{subreddit}/top.json", {"limit": 100, "t": "week"}),
            (f"/r/{subreddit}/top.json", {"limit": 100, "t": "year"}),
            (f"/r/{subreddit}/top.json", {"limit": 100, "t": "all"}),
            (f"/r/{subreddit}/hot.json", {"limit": 100}),
        ]

    def _collect_archive(
        self, subreddits: list[str], now: float
    ) -> tuple[list[Story], int, list[str]]:
        selected: dict[str, Story] = {}
        failures: list[str] = []
        seen = 0
        eval_cfg = self._eval_cfg()
        window_start = recent_window_start(now, int(self._rules()["recent_post_days"]))
        hot_pages = int(self.cfg.get("archive_hot_pages", 3))
        long_pages = int(self.cfg.get("archive_long_tail_pages", 5))
        long_lookback_days = int(self.cfg.get("archive_long_tail_lookback_days", 3650))

        total = len(subreddits)
        for index, sub in enumerate(subreddits, start=1):
            print(f"[reddit] archive {index}/{total} r/{sub}", flush=True)
            try:
                hot_stories = self._archive_search_pages(
                    subreddit=sub,
                    after=int(window_start),
                    before=int(now) + 1,
                    pages=hot_pages,
                )
                long_stories = self._archive_search_pages(
                    subreddit=sub,
                    after=int(now - long_lookback_days * 86400),
                    before=int(now) + 1,
                    pages=long_pages,
                )
                for story in [*hot_stories, *long_stories]:
                    if title_has_excluded_prefix(
                        story.title,
                        self.cfg.get("exclude_title_prefixes", []),
                    ):
                        continue
                    _, hot_min, _ = reddit_thresholds(story, eval_cfg)
                    if story.reply_count <= hot_min:
                        continue
                    seen += 1
                    accepted, _ = evaluate(story, eval_cfg, now)
                    if accepted:
                        previous = selected.get(story.url)
                        if not previous or story.reply_count > previous.reply_count:
                            selected[story.url] = story
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{sub}:{exc}")
        return self._sort(list(selected.values())), seen, failures

    def _archive_search_pages(
        self,
        subreddit: str,
        after: int,
        before: int,
        pages: int,
    ) -> list[Story]:
        stories: list[Story] = []
        cursor_before = before
        page_size = int(self.cfg.get("archive_page_size", 100))
        for _ in range(pages):
            data = self.client.get_json(
                f"{self._archive_base()}/api/posts/search",
                params={
                    "subreddit": subreddit,
                    "after": after,
                    "before": cursor_before,
                    "limit": page_size,
                    "sort": "desc",
                },
            )
            batch = parse_archive_listing(data, subreddit)
            if not batch:
                break
            stories.extend(batch)
            dated = [story.posted_at for story in batch if story.posted_at]
            if not dated:
                break
            oldest = min(dated)
            if len(batch) < page_size or oldest <= after:
                break
            cursor_before = int(oldest)
        return stories

    def _prepare_percentiles(self) -> str:
        parent = self.cfg.get("_parent_cfg")
        if isinstance(parent, dict):
            out_root = output_dir(parent)
            full_cfg = parent
        else:
            out_root = os.path.join(SKILL_ROOT, "output")
            full_cfg = {
                "reddit": self.cfg,
                "rules": {"reddit": self._rules()["reddit"]},
            }
        cache_name = self.cfg.get("percentile_cache_file", "reddit_percentiles.json")
        ttl_hours = float(self.cfg.get("percentile_cache_ttl_hours", 24))
        cached = load_cached_percentiles(out_root, cache_name)
        if cached and self._percentile_cache_fresh(cached, ttl_hours):
            self.last_percentiles = cached
            self._p95_map = p95_map_from_document(cached)
            age_h = self._percentile_cache_age_hours(cached)
            print(
                f"[reddit] P95 cache hit ({len(self._p95_map)} subs, "
                f"age={age_h:.1f}h < ttl={ttl_hours}h)",
                flush=True,
            )
            return (
                f"reused Reddit P95 cache ({len(self._p95_map)} subs, "
                f"age={age_h:.1f}h < ttl={ttl_hours}h)"
            )
        try:
            print("[reddit] refreshing P95 percentiles …", flush=True)
            document = refresh_reddit_percentiles(full_cfg, self.client, out_root)
            self.last_percentiles = document
            self._p95_map = p95_map_from_document(document)
            return f"refreshed Reddit P95 cache ({len(self._p95_map)} subs)"
        except Exception as exc:  # noqa: BLE001
            if cached:
                self.last_percentiles = cached
                self._p95_map = p95_map_from_document(cached)
                return f"percentile refresh failed ({exc}); using cached P95"
            self.last_percentiles = None
            self._p95_map = {}
            return f"percentile refresh failed ({exc}); floors only"

    @staticmethod
    def _percentile_cache_age_hours(document: dict[str, Any]) -> float:
        from datetime import datetime

        raw = str(document.get("generated_at") or "").strip()
        if not raw:
            return float("inf")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                generated = datetime.strptime(raw[:19], fmt)
                return max(0.0, (datetime.now() - generated).total_seconds() / 3600.0)
            except ValueError:
                continue
        return float("inf")

    def _percentile_cache_fresh(self, document: dict[str, Any], ttl_hours: float) -> bool:
        if ttl_hours <= 0:
            return False
        return self._percentile_cache_age_hours(document) < ttl_hours

    def _eval_cfg(self) -> dict[str, Any]:
        return {"rules": self._rules(), "reddit_p95": self._p95_map}

    def _archive_base(self) -> str:
        return self.cfg.get(
            "archive_url", "https://arctic-shift.photon-reddit.com"
        ).rstrip("/")

    def _rules(self) -> dict[str, Any]:
        return self.cfg["_global_rules"]

    @staticmethod
    def _sort(stories: list[Story]) -> list[Story]:
        stories.sort(
            key=lambda story: (
                story.track == "long_tail",
                story.reply_count,
                story.posted_at,
            ),
            reverse=True,
        )
        return stories
