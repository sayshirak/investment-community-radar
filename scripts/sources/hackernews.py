"""Hacker News adapter for /front, /ask, and /show."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from filtering import evaluate, is_hn_tech_discussion
from models import SourceHealth, Story
from sources.base import SourceAdapter

_COMMENT_RE = re.compile(r"(\d+)\s+comments?", re.I)
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str, limit: int = 500) -> str:
    text = unescape(value or "")
    text = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


def parse_front_listing(html: str, site: str = "https://news.ycombinator.com") -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for item in soup.select("tr.athing"):
        item_id = str(item.get("id") or "").strip()
        if not item_id.isdigit():
            continue
        title_anchor = item.select_one(".titleline > a")
        if not title_anchor:
            continue
        title = title_anchor.get_text(" ", strip=True)
        href = str(title_anchor.get("href") or "").strip()
        if not href:
            continue
        external_url = ""
        if href.startswith("http://") or href.startswith("https://"):
            external_url = href
            discussion_url = f"{site.rstrip('/')}/item?id={item_id}"
        else:
            discussion_url = urljoin(site, href)

        sub = item.find_next_sibling("tr")
        if sub is None:
            continue
        sub_text = sub.get_text(" ", strip=True)
        comments_match = _COMMENT_RE.search(sub_text)
        reply_count = int(comments_match.group(1)) if comments_match else 0
        age = sub.select_one("span.age")
        posted_at = 0.0
        if age and age.get("title"):
            posted_at = _parse_hn_age(str(age.get("title")))
        rows.append(
            {
                "item_id": item_id,
                "board": "front",
                "title": title,
                "url": discussion_url,
                "external_url": external_url,
                "reply_count": reply_count,
                "posted_at": posted_at,
                "summary": "",
            }
        )
    return rows


def parse_algolia_hits(data: Any, board: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise ValueError("schema_mismatch: Algolia hits missing")
    rows: list[dict[str, Any]] = []
    for hit in data["hits"]:
        if not isinstance(hit, dict):
            continue
        object_id = str(hit.get("objectID") or hit.get("story_id") or "").strip()
        title = str(hit.get("title") or "").strip()
        if not object_id or not title:
            continue
        created = float(hit.get("created_at_i") or 0)
        summary = _clean_text(str(hit.get("story_text") or ""))
        external_url = str(hit.get("url") or "").strip()
        rows.append(
            {
                "item_id": object_id,
                "board": board,
                "title": title,
                "url": f"https://news.ycombinator.com/item?id={object_id}",
                "external_url": external_url if board == "front" else "",
                "reply_count": int(hit.get("num_comments") or 0),
                "posted_at": created,
                "summary": summary,
            }
        )
    return rows


def summarize_external_html(html: str, url: str = "") -> str:
    """Extract a short public page summary for /front outbound links."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    parts: list[str] = []
    title = soup.find("title")
    if title:
        text = title.get_text(" ", strip=True)
        if text:
            parts.append(text)

    for selector in (
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    ):
        meta = soup.select_one(selector)
        if meta and meta.get("content"):
            content = str(meta.get("content")).strip()
            if content and content not in parts:
                parts.append(content)
                break

    paragraphs = []
    for node in soup.select("article p, main p, p"):
        text = node.get_text(" ", strip=True)
        if len(text) < 40:
            continue
        paragraphs.append(text)
        if len(paragraphs) >= 3:
            break
    if paragraphs:
        parts.append(" ".join(paragraphs))

    summary = _clean_text(" — ".join(parts))
    if not summary and url:
        summary = f"外链页面：{url}"
    return summary[:500]


def _parse_hn_age(value: str) -> float:
    # "2026-08-01T18:07:07" or "2026-08-01T18:07:07 1785607627"
    token = value.strip().split()[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(token, fmt).timestamp()
        except ValueError:
            continue
    try:
        return float(value.strip().split()[-1])
    except ValueError:
        return 0.0


def _is_fetchable_external(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    if host.endswith("ycombinator.com") or host.endswith("news.ycombinator.com"):
        return False
    path = (parsed.path or "").lower()
    if any(path.endswith(ext) for ext in (".pdf", ".zip", ".gz", ".tgz", ".png", ".jpg", ".jpeg", ".gif", ".mp4")):
        return False
    return True


class HackerNewsSource(SourceAdapter):
    name = "hackernews"

    def probe(self) -> SourceHealth:
        site = self.cfg.get("site", "https://news.ycombinator.com").rstrip("/")
        try:
            html = self.client.get_text(f"{site}/front")
            rows = parse_front_listing(html, site)
            if not rows:
                raise ValueError("schema_mismatch: no HN front stories found")
            return SourceHealth(
                self.name,
                True,
                candidates_seen=len(rows),
                reason="/front HTML + Algolia ask/show",
            )
        except Exception as exc:  # noqa: BLE001
            return SourceHealth(self.name, False, reason=str(exc))

    def collect(self, now: float) -> tuple[list[Story], SourceHealth]:
        rules = self._rules()
        hn_rules = rules["hackernews"]
        lookback_days = int(hn_rules.get("post_lookback_days", 30))
        # "超过 N" is strict; keep candidates with replies >= N+1.
        min_replies = int(
            hn_rules.get(
                "hot_replies_strictly_over",
                hn_rules.get("hot_replies_at_least", 100),
            )
        ) + 1
        window_start = (
            datetime.fromtimestamp(now)
            .astimezone()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=lookback_days - 1)
        ).timestamp()

        candidates: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        listings_ok = 0

        try:
            print("[hackernews] Algolia ask …", flush=True)
            ask_rows = self._collect_algolia("ask", "ask_hn", window_start, min_replies)
            listings_ok += 1
            self._merge_candidates(candidates, ask_rows)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ask:{exc}")

        try:
            print("[hackernews] Algolia show …", flush=True)
            show_rows = self._collect_algolia("show", "show_hn", window_start, min_replies)
            listings_ok += 1
            self._merge_candidates(candidates, show_rows)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"show:{exc}")

        try:
            print("[hackernews] /front lookback …", flush=True)
            front_rows = self._collect_front(now, lookback_days, min_replies)
            listings_ok += 1
            self._merge_candidates(candidates, front_rows)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"front:{exc}")

        selected: list[Story] = []
        summary_failures = 0
        # Skip live external-page fetches during collect: hundreds of front
        # candidates × polite interval/timeout dominated wall-clock (often >20min).
        # Algolia rows already carry story_text; front rows use title-only filtering.
        for row in candidates.values():
            if row["reply_count"] < min_replies:
                continue
            if row["posted_at"] and row["posted_at"] < window_start:
                continue

            summary = str(row.get("summary") or "")
            if not summary and row.get("external_url"):
                summary = f"外链未抓取摘要：{row['external_url']}"

            if is_hn_tech_discussion(
                row["title"],
                summary,
                {**self.cfg, "_board": row.get("board")},
            ):
                continue

            story = Story(
                platform="hackernews",
                community=str(row["board"]),
                title=str(row["title"]),
                summary=summary[:500],
                url=str(row["url"]),
                posted_at=float(row.get("posted_at") or 0),
                last_reply_at=float(row.get("posted_at") or 0),
                reply_count=int(row["reply_count"]),
                source_priority=row.get("board") in ("front", "ask"),
            )
            accepted, _ = evaluate(story, {"rules": rules}, now)
            if accepted:
                selected.append(story)

        selected.sort(
            key=lambda story: (
                story.track == "long_tail",
                story.reply_count,
                story.posted_at,
            ),
            reverse=True,
        )

        if not listings_ok:
            return [], SourceHealth(
                self.name,
                False,
                len(candidates),
                0,
                "; ".join(failures) or "all HN boards failed",
            )

        reason_parts = []
        if failures:
            reason_parts.append(f"partial board failures={len(failures)}")
        if summary_failures:
            reason_parts.append(f"external summary failures={summary_failures}")
        return selected, SourceHealth(
            self.name,
            True,
            len(candidates),
            len(selected),
            "; ".join(reason_parts),
        )

    def _collect_algolia(
        self,
        board: str,
        tag: str,
        window_start: float,
        min_replies: int,
    ) -> list[dict[str, Any]]:
        algolia = self.cfg.get("algolia_url", "https://hn.algolia.com/api/v1/search_by_date")
        page_size = int(self.cfg.get("algolia_hits_per_page", 50))
        max_pages = int(self.cfg.get("algolia_max_pages", 5))
        rows: list[dict[str, Any]] = []
        for page in range(max_pages):
            data = self.client.get_json(
                algolia,
                params={
                    "tags": tag,
                    "numericFilters": (
                        f"created_at_i>{int(window_start)},num_comments>={min_replies}"
                    ),
                    "hitsPerPage": page_size,
                    "page": page,
                },
            )
            batch = parse_algolia_hits(data, board)
            rows.extend(batch)
            nb_pages = int(data.get("nbPages") or 0) if isinstance(data, dict) else 0
            if page + 1 >= nb_pages or not batch:
                break
        return rows

    def _collect_front(
        self,
        now: float,
        lookback_days: int,
        min_replies: int,
    ) -> list[dict[str, Any]]:
        site = self.cfg.get("site", "https://news.ycombinator.com").rstrip("/")
        local = datetime.fromtimestamp(now).astimezone().date()
        pages_per_day = int(self.cfg.get("front_pages_per_day", 1))
        rows: list[dict[str, Any]] = []
        for offset in range(lookback_days):
            day = local - timedelta(days=offset)
            day_text = day.isoformat()
            if offset == 0 or (offset + 1) % 5 == 0 or offset + 1 == lookback_days:
                print(
                    f"[hackernews] /front day {offset + 1}/{lookback_days} ({day_text})",
                    flush=True,
                )
            for page in range(1, pages_per_day + 1):
                params: dict[str, Any] = {"day": day_text}
                if page > 1:
                    params["p"] = page
                try:
                    html = self.client.get_text(f"{site}/front", params=params)
                except Exception as exc:  # noqa: BLE001
                    print(f"[hackernews] /front skip {day_text}: {exc}", flush=True)
                    break
                parsed = parse_front_listing(html, site)
                if not parsed:
                    break
                for row in parsed:
                    if row["reply_count"] >= min_replies:
                        rows.append(row)
        return rows

    @staticmethod
    def _merge_candidates(
        store: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            key = str(row["url"])
            previous = store.get(key)
            if not previous or row["reply_count"] > previous["reply_count"]:
                store[key] = row
            elif previous.get("board") != "front" and row.get("board") == "front":
                # Prefer front labeling when the same item appears on front.
                previous["board"] = "front"
                if row.get("external_url"):
                    previous["external_url"] = row["external_url"]

    def _rules(self) -> dict[str, Any]:
        return self.cfg["_global_rules"]
