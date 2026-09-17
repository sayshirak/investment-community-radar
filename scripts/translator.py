"""Optional no-key English-to-Chinese translation for selected Reddit posts."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from http_client import PoliteClient
from models import Story

_SPLIT = "__RADAR_SPLIT_9F4A__"


class RedditTranslator:
    def __init__(
        self,
        cfg: dict[str, Any],
        client: PoliteClient,
        output_root: str,
    ):
        self.cfg = cfg
        self.client = client
        self.enabled = bool(cfg.get("enabled", True))
        self.endpoint = cfg.get(
            "endpoint", "https://translate.googleapis.com/translate_a/single"
        )
        self.cache_path = os.path.join(
            output_root, cfg.get("cache_file", "translation_cache.json")
        )
        self.cache = self._load_cache()
        self.failures = 0
        self.translated = 0
        self.cache_hits = 0
        self._circuit_open = False

    def translate_all(self, stories: list[Story]) -> None:
        if not self.enabled:
            return
        changed = False
        for story in stories:
            if story.platform not in ("reddit", "hackernews"):
                continue
            cache_key = self._key(story.title, story.summary)
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict):
                story.title_zh = str(cached.get("title_zh") or "")
                story.summary_zh = str(cached.get("summary_zh") or "")
                self.cache_hits += 1
                continue
            if self._circuit_open:
                continue
            try:
                title_zh, summary_zh = self._translate_pair(story.title, story.summary)
            except Exception:  # noqa: BLE001
                self.failures += 1
                # Avoid retry storms when the public endpoint is unavailable.
                if self.failures >= int(self.cfg.get("circuit_breaker_failures", 3)):
                    self._circuit_open = True
                continue
            story.title_zh = title_zh
            story.summary_zh = summary_zh
            self.cache[cache_key] = {
                "title_zh": title_zh,
                "summary_zh": summary_zh,
            }
            self.translated += 1
            changed = True
        if changed:
            self._save_cache()

    def translate_document(self, document: dict[str, Any]) -> None:
        """Backfill Chinese fields in an already collected hotspots document."""
        if not self.enabled:
            return
        changed = False
        for platform in ("reddit", "hackernews"):
            section = document.get("stories", {}).get(platform, {})
            for track in ("long_tail", "recent"):
                for row in section.get(track, []):
                    title = str(row.get("title") or "")
                    summary = str(row.get("summary") or "")
                    cache_key = self._key(title, summary)
                    cached = self.cache.get(cache_key)
                    if isinstance(cached, dict):
                        row["title_zh"] = str(cached.get("title_zh") or "")
                        row["summary_zh"] = str(cached.get("summary_zh") or "")
                        self.cache_hits += 1
                        continue
                    if self._circuit_open:
                        row.setdefault("title_zh", "")
                        row.setdefault("summary_zh", "")
                        continue
                    try:
                        title_zh, summary_zh = self._translate_pair(title, summary)
                    except Exception:  # noqa: BLE001
                        self.failures += 1
                        row.setdefault("title_zh", "")
                        row.setdefault("summary_zh", "")
                        if self.failures >= int(self.cfg.get("circuit_breaker_failures", 3)):
                            self._circuit_open = True
                        continue
                    row["title_zh"] = title_zh
                    row["summary_zh"] = summary_zh
                    self.cache[cache_key] = {
                        "title_zh": title_zh,
                        "summary_zh": summary_zh,
                    }
                    self.translated += 1
                    changed = True
        if changed:
            self._save_cache()

    def status_text(self) -> str:
        if not self.enabled:
            return "translation disabled"
        parts = [
            f"translated={self.translated}",
            f"translation_cache_hits={self.cache_hits}",
        ]
        if self.failures:
            parts.append(f"translation_failures={self.failures}")
        return ", ".join(parts)

    def _translate_pair(self, title: str, summary: str) -> tuple[str, str]:
        source = title if not summary else f"{title}\n{_SPLIT}\n{summary}"
        data = self.client.get_json(
            self.endpoint,
            params={
                "client": "gtx",
                "sl": "en",
                "tl": self.cfg.get("target_language", "zh-CN"),
                "dt": "t",
                "q": source,
            },
        )
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise ValueError("translation schema mismatch")
        translated = "".join(
            str(segment[0])
            for segment in data[0]
            if isinstance(segment, list) and segment and segment[0]
        ).strip()
        if not translated:
            raise ValueError("empty translation")
        if summary:
            if _SPLIT not in translated:
                raise ValueError("translation split marker missing")
            title_zh, summary_zh = translated.split(_SPLIT, 1)
            return title_zh.strip(), summary_zh.strip()
        return translated, ""

    def _load_cache(self) -> dict[str, dict[str, str]]:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        temporary = f"{self.cache_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.cache, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.cache_path)

    @staticmethod
    def _key(title: str, summary: str) -> str:
        return hashlib.sha256(f"{title}\0{summary}".encode("utf-8")).hexdigest()
