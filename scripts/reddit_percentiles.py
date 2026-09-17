"""Estimate per-subreddit all-time comment-count percentiles for Reddit."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from typing import Any

from http_client import PoliteClient
from models import format_ts


def percentile_nearest_rank(sorted_values: list[int], percentile: float) -> int:
    """Inclusive nearest-rank percentile on a pre-sorted ascending list."""
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return int(sorted_values[0])
    rank = percentile / 100.0 * (len(sorted_values) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return int(sorted_values[lo])
    value = sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (rank - lo)
    return int(round(value))


def refresh_reddit_percentiles(
    cfg: dict[str, Any],
    client: PoliteClient,
    output_root: str,
) -> dict[str, Any]:
    """Recompute per-subreddit P95 via yearly stratified archive samples and cache.

    Each year contributes up to 100 oldest + 100 newest posts (Arctic Shift).
    Result is written to output/ so collect stays inspectable without full dumps.
    """
    reddit_cfg = cfg.get("reddit", {})
    rules = cfg.get("rules", {}).get("reddit", {})
    percentile = float(rules.get("top_percentile", reddit_cfg.get("top_percentile", 95)))
    start_year = int(reddit_cfg.get("percentile_start_year", 2012))
    archive = str(reddit_cfg.get("archive_url", "https://arctic-shift.photon-reddit.com")).rstrip(
        "/"
    )
    subreddits = list(reddit_cfg.get("subreddits", []))
    now = time.time()
    current_year = datetime.fromtimestamp(now).year

    by_sub: dict[str, dict[str, Any]] = {}
    for subreddit in subreddits:
        counts = _sample_comment_counts(
            client=client,
            archive=archive,
            subreddit=subreddit,
            start_year=start_year,
            end_year=current_year,
            now=now,
        )
        counts.sort()
        p95 = percentile_nearest_rank(counts, percentile) if counts else 0
        by_sub[subreddit] = {
            "p95": p95,
            "sample_size": len(counts),
            "p50": counts[len(counts) // 2] if counts else 0,
            "max": counts[-1] if counts else 0,
        }

    document = {
        "generated_at": format_ts(now),
        "method": "yearly_stratified_asc_desc",
        "percentile": percentile,
        "start_year": start_year,
        "subreddits": by_sub,
    }
    cache_name = reddit_cfg.get("percentile_cache_file", "reddit_percentiles.json")
    cache_path = os.path.join(output_root, cache_name)
    os.makedirs(output_root, exist_ok=True)
    temporary = f"{cache_path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, cache_path)
    document["cache_path"] = cache_path
    return document


def p95_map_from_document(document: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, row in (document.get("subreddits") or {}).items():
        if isinstance(row, dict):
            out[str(name)] = int(row.get("p95") or 0)
    return out


def load_cached_percentiles(output_root: str, cache_name: str) -> dict[str, Any] | None:
    path = os.path.join(output_root, cache_name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _sample_comment_counts(
    client: PoliteClient,
    archive: str,
    subreddit: str,
    start_year: int,
    end_year: int,
    now: float,
) -> list[int]:
    counts: list[int] = []
    for year in range(start_year, end_year + 1):
        after = datetime(year, 1, 1).timestamp()
        before = datetime(year + 1, 1, 1).timestamp()
        before = min(before, now + 1)
        if after >= before:
            continue
        for sort in ("asc", "desc"):
            try:
                data = client.get_json(
                    f"{archive}/api/posts/search",
                    params={
                        "subreddit": subreddit,
                        "after": int(after),
                        "before": int(before),
                        "limit": 100,
                        "sort": sort,
                        "fields": "created_utc,num_comments",
                    },
                )
            except Exception:  # noqa: BLE001
                continue
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                continue
            for post in rows:
                if not isinstance(post, dict):
                    continue
                counts.append(int(post.get("num_comments") or 0))
    return counts
