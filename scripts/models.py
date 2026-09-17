"""Normalized records used by the Reddit + V2EX + Hacker News radar."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def format_ts(value: float) -> str:
    if not value:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


@dataclass
class Story:
    platform: str
    community: str
    title: str
    summary: str
    url: str
    posted_at: float
    last_reply_at: float
    reply_count: int
    reply_count_is_lower_bound: bool = False
    source_priority: bool = False
    title_zh: str = ""
    summary_zh: str = ""
    material_type: str = "其他故事"
    track: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def post_age_days(self) -> float:
        return max(0.0, (time.time() - self.posted_at) / 86400) if self.posted_at else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "platform": self.platform,
            "community": self.community,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "posted_at": format_ts(self.posted_at),
            "last_reply_at": format_ts(self.last_reply_at),
            "reply_count": self.reply_count,
            "reply_count_is_lower_bound": self.reply_count_is_lower_bound,
            "source_priority": self.source_priority,
            "material_type": self.material_type,
            "track": self.track,
            "reasons": list(self.reasons),
        }
        if self.platform in ("reddit", "hackernews"):
            data["title_zh"] = self.title_zh
            data["summary_zh"] = self.summary_zh
        return data


@dataclass
class SourceHealth:
    name: str
    ok: bool
    candidates_seen: int = 0
    stories_selected: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "candidates_seen": self.candidates_seen,
            "stories_selected": self.stories_selected,
            "reason": self.reason,
        }
