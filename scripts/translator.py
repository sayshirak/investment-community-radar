"""Optional no-key English-to-Chinese translation for selected Reddit/HN posts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import quote

from http_client import FetchError, PoliteClient
from models import Story

# Transport / endpoint issues trip the circuit; per-item parse issues do not.
_CIRCUIT_KINDS = frozenset(
    {
        "timeout",
        "network",
        "http_429",
        "http_5xx",
        "http_403",
        "http_4xx",
        "decode",
        "empty_body",
    }
)

_DEFAULT_PRIMARY = "https://translate.googleapis.com/translate_a/single"
_DEFAULT_FALLBACKS = [
    {
        "name": "google_clients5",
        "kind": "google_gtx",
        "url": "https://clients5.google.com/translate_a/t",
    },
    {
        "name": "mymemory",
        "kind": "mymemory",
        "url": "https://api.mymemory.translated.net/get",
    },
    {
        "name": "lingva",
        "kind": "lingva",
        "url": "https://lingva.ml/api/v1",
    },
]


def build_translation_client(cfg: dict[str, Any]) -> PoliteClient:
    """Dedicated client so crawl rate limits / proxies do not starve translation."""
    translation = cfg.get("translation", {})
    base = cfg.get("request", {})
    request: dict[str, Any] = {
        "user_agent": base.get(
            "user_agent", "investment-community-radar/2.0"
        ),
        "timeout_seconds": float(translation.get("timeout_seconds", 15)),
        "min_interval_seconds": float(translation.get("min_interval_seconds", 0.4)),
        "max_retries": int(translation.get("max_retries", 3)),
        "backoff_seconds": float(translation.get("backoff_seconds", 1.5)),
        # Keep crawl and translation proxy choices independent.
        "trust_env": bool(translation.get("trust_env", False)),
    }
    proxies = translation.get("proxies")
    proxy = translation.get("proxy") or os.environ.get("RADAR_TRANSLATION_PROXY")
    if isinstance(proxies, dict):
        request["proxies"] = proxies
    elif proxy:
        request["proxy"] = str(proxy).strip()
    return PoliteClient({"request": request})


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
        self.endpoints = self._build_endpoints(cfg)
        self.cache_path = os.path.join(
            output_root, cfg.get("cache_file", "translation_cache.json")
        )
        self.cache = self._load_cache()
        self.failures = 0
        self.circuit_failures = 0
        self.translated = 0
        self.cache_hits = 0
        self.fallback_uses = 0
        self._circuit_open_until = 0.0
        self._last_error = ""
        self._last_endpoint = ""
        # name -> monotonic deadline while endpoint is cooled down after 429/5xx
        self._endpoint_cooldown: dict[str, float] = {}

    def translate_all(self, stories: list[Story]) -> None:
        if not self.enabled:
            return
        changed = False
        pending = [
            story
            for story in stories
            if story.platform in ("reddit", "hackernews")
        ]
        # Short texts first so early transport flakes leave fewer blanks.
        pending.sort(key=lambda s: len(s.title or "") + len(s.summary or ""))
        for story in pending:
            cache_key = self._key(story.title, story.summary)
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict):
                story.title_zh = str(cached.get("title_zh") or "")
                story.summary_zh = str(cached.get("summary_zh") or "")
                self.cache_hits += 1
                continue
            if self._circuit_blocks():
                continue
            try:
                title_zh, summary_zh = self._translate_pair(story.title, story.summary)
            except Exception as exc:  # noqa: BLE001
                self._note_failure(exc)
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
        rows: list[dict[str, Any]] = []
        for platform in ("reddit", "hackernews"):
            section = document.get("stories", {}).get(platform, {})
            for track in ("long_tail", "recent"):
                rows.extend(section.get(track, []))
        rows.sort(
            key=lambda row: len(str(row.get("title") or ""))
            + len(str(row.get("summary") or ""))
        )
        for row in rows:
            title = str(row.get("title") or "")
            summary = str(row.get("summary") or "")
            cache_key = self._key(title, summary)
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict):
                row["title_zh"] = str(cached.get("title_zh") or "")
                row["summary_zh"] = str(cached.get("summary_zh") or "")
                self.cache_hits += 1
                continue
            if self._circuit_blocks():
                row.setdefault("title_zh", "")
                row.setdefault("summary_zh", "")
                continue
            try:
                title_zh, summary_zh = self._translate_pair(title, summary)
            except Exception as exc:  # noqa: BLE001
                self._note_failure(exc)
                row.setdefault("title_zh", "")
                row.setdefault("summary_zh", "")
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
        if self.fallback_uses:
            parts.append(f"translation_fallback_uses={self.fallback_uses}")
        if self.failures:
            parts.append(f"translation_failures={self.failures}")
        if self._circuit_blocks():
            parts.append("translation_circuit_open=true")
        if self._last_endpoint:
            parts.append(f"translation_last_endpoint={self._last_endpoint}")
        if self._last_error:
            parts.append(f"translation_last_error={self._last_error}")
        return ", ".join(parts)

    def _translate_pair(self, title: str, summary: str) -> tuple[str, str]:
        """Translate title and summary as separate requests (no fragile split marker)."""
        title_zh = self._translate_text(title) if title.strip() else ""
        if not summary.strip():
            return title_zh, ""
        try:
            summary_zh = self._translate_text(summary)
        except Exception as exc:  # noqa: BLE001
            # Keep title progress; summary-only soft failures do not fail the pair.
            self._note_failure(exc, soft=True)
            return title_zh, ""
        return title_zh, summary_zh

    def _translate_text(self, text: str) -> str:
        max_chars = int(self.cfg.get("max_summary_chars", 1200))
        source = text.strip()
        if len(source) > max_chars:
            source = source[:max_chars]
        if not source:
            return ""

        errors: list[Exception] = []
        for index, spec in enumerate(self.endpoints):
            name = str(spec["name"])
            if self._endpoint_is_cooling(name):
                continue
            try:
                translated = self._translate_via(spec, source)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                self._maybe_cool_endpoint(name, exc)
                continue
            self._last_endpoint = name
            if index > 0:
                self.fallback_uses += 1
            return translated

        if errors:
            raise errors[-1]
        raise FetchError("network", "no translation endpoints available")

    def _translate_via(self, spec: dict[str, str], source: str) -> str:
        kind = spec["kind"]
        url = spec["url"]
        target = str(self.cfg.get("target_language", "zh-CN"))
        if kind == "google_gtx":
            data = self.client.get_json(
                url,
                params={
                    "client": "gtx",
                    "sl": "en",
                    "tl": target,
                    "dt": "t",
                    "q": source,
                },
            )
            return self._parse_google_payload(data)
        if kind == "mymemory":
            # Free tier rejects very long queries; keep a safer cap.
            clipped = source[:450]
            data = self.client.get_json(
                url,
                params={"q": clipped, "langpair": f"en|{target}"},
            )
            return self._parse_mymemory_payload(data)
        if kind == "lingva":
            base = url.rstrip("/")
            # Lingva puts the text in the path; keep URL length reasonable.
            clipped = source[:800]
            lingva_target = _lingva_language_code(target)
            path = f"{base}/en/{lingva_target}/{quote(clipped, safe='')}"
            data = self.client.get_json(path)
            return self._parse_lingva_payload(data, source=clipped)
        raise ValueError(f"unknown translation kind: {kind}")

    @staticmethod
    def _parse_google_payload(data: Any) -> str:
        if isinstance(data, list) and data:
            # clients5 /translate_a/t often returns ["中文", null, "en"]
            if isinstance(data[0], str):
                translated = data[0].strip()
                if translated:
                    return translated
            if isinstance(data[0], list):
                # [["中文", "English", ...], ...] or [[["中文", ...], ...], ...]
                first = data[0]
                if first and isinstance(first[0], str):
                    translated = str(first[0]).strip()
                    if translated:
                        return translated
                translated = "".join(
                    str(segment[0])
                    for segment in first
                    if isinstance(segment, list) and segment and segment[0]
                ).strip()
                if translated:
                    return translated
        raise ValueError("translation schema mismatch")

    @staticmethod
    def _parse_mymemory_payload(data: Any) -> str:
        if not isinstance(data, dict):
            raise ValueError("translation schema mismatch")
        status = data.get("responseStatus")
        if status not in (None, 200, "200"):
            raise FetchError("http_4xx", f"mymemory status {status}")
        payload = data.get("responseData")
        if not isinstance(payload, dict):
            raise ValueError("translation schema mismatch")
        translated = str(payload.get("translatedText") or "").strip()
        if not translated:
            raise ValueError("empty translation")
        if translated.upper().startswith("MYMEMORY WARNING"):
            raise FetchError("http_429", translated[:160])
        return translated

    @staticmethod
    def _parse_lingva_payload(data: Any, *, source: str = "") -> str:
        if not isinstance(data, dict):
            raise ValueError("translation schema mismatch")
        if data.get("error"):
            raise FetchError("http_4xx", f"lingva error: {data.get('error')}")
        translated = str(data.get("translation") or "").strip()
        if not translated:
            raise ValueError("empty translation")
        # Some public Lingva mirrors echo the source when upstream translation fails.
        if source and translated == source.strip():
            raise ValueError("lingva returned untranslated source")
        return translated

    def _endpoint_is_cooling(self, name: str) -> bool:
        until = self._endpoint_cooldown.get(name, 0.0)
        if until <= 0:
            return False
        if time.monotonic() >= until:
            self._endpoint_cooldown.pop(name, None)
            return False
        return True

    def _maybe_cool_endpoint(self, name: str, exc: Exception) -> None:
        kind = getattr(exc, "kind", None)
        if not (isinstance(exc, FetchError) and kind in _CIRCUIT_KINDS):
            return
        cooldown = float(self.cfg.get("endpoint_cooldown_seconds", 90))
        if cooldown <= 0:
            return
        self._endpoint_cooldown[name] = time.monotonic() + cooldown

    @staticmethod
    def _build_endpoints(cfg: dict[str, Any]) -> list[dict[str, str]]:
        specs: list[dict[str, str]] = []
        primary = cfg.get("endpoint", _DEFAULT_PRIMARY)
        if primary:
            specs.append(
                {
                    "name": "primary",
                    "kind": "google_gtx",
                    "url": str(primary),
                }
            )
        fallbacks = cfg.get("fallback_endpoints")
        if fallbacks is None:
            fallbacks = list(_DEFAULT_FALLBACKS)
        for index, item in enumerate(fallbacks):
            if isinstance(item, str):
                url = item
                kind = RedditTranslator._infer_kind(url)
                name = f"fallback_{index + 1}"
            elif isinstance(item, dict) and item.get("url"):
                url = str(item["url"])
                kind = str(item.get("kind") or RedditTranslator._infer_kind(url))
                name = str(item.get("name") or f"fallback_{index + 1}")
            else:
                continue
            specs.append({"name": name, "kind": kind, "url": url})
        # De-dupe by URL while preserving order.
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for spec in specs:
            url = spec["url"]
            if url in seen:
                continue
            seen.add(url)
            unique.append(spec)
        return unique

    @staticmethod
    def _infer_kind(url: str) -> str:
        lowered = url.lower()
        if "mymemory.translated.net" in lowered:
            return "mymemory"
        if "lingva" in lowered:
            return "lingva"
        return "google_gtx"

    def _circuit_blocks(self) -> bool:
        if self._circuit_open_until <= 0:
            return False
        if time.monotonic() >= self._circuit_open_until:
            self._circuit_open_until = 0.0
            return False
        return True

    def _note_failure(self, exc: Exception, *, soft: bool = False) -> None:
        self.failures += 1
        self._last_error = str(exc)[:180].replace("\n", " ")
        if soft:
            return
        kind = getattr(exc, "kind", None)
        if isinstance(exc, FetchError) and kind in _CIRCUIT_KINDS:
            self.circuit_failures += 1
        elif isinstance(exc, FetchError):
            return
        else:
            # Per-item ValueError / schema issues: count in failures, do not trip.
            return
        threshold = int(self.cfg.get("circuit_breaker_failures", 12))
        if self.circuit_failures >= threshold:
            cooldown = float(self.cfg.get("circuit_breaker_cooldown_seconds", 45))
            self._circuit_open_until = time.monotonic() + cooldown
            self.circuit_failures = 0

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


def _lingva_language_code(target: str) -> str:
    normalized = (target or "zh").strip()
    if normalized.lower() in {"zh-cn", "zh-tw", "zh-hans", "zh-hant"}:
        return "zh"
    return normalized or "zh"
