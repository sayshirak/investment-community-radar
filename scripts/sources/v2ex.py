"""V2EX HTML adapter."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from filtering import evaluate, is_v2ex_excluded_ad
from models import SourceHealth, Story
from sources.base import SourceAdapter

_TOPIC_RE = re.compile(r"^/t/(\d+)")


def parse_v2ex_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S %z").timestamp()
    except ValueError:
        return 0.0


def parse_listing(
    html: str,
    base_url: str,
    source_name: str,
    priority_nodes: set[str],
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for anchor in soup.select("a.topic-link"):
        href = anchor.get("href", "")
        match = _TOPIC_RE.match(href)
        if not match:
            continue
        container = anchor.find_parent("div", class_="cell")
        if not container:
            continue
        count_anchor = container.select_one("a.count_livid")
        count_text = count_anchor.get_text(strip=True) if count_anchor else "0"
        try:
            replies = int(count_text)
        except ValueError:
            replies = 0

        if source_name == "recent":
            node_anchor = container.select_one("a.node")
            community = (
                node_anchor.get("href", "").split("/go/", 1)[-1]
                if node_anchor and "/go/" in node_anchor.get("href", "")
                else "recent"
            )
        else:
            community = source_name

        ago = container.select_one("span.ago[title]")
        rows.append(
            {
                "community": community,
                "title": anchor.get_text(" ", strip=True),
                "url": urljoin(base_url, f"/t/{match.group(1)}"),
                "reply_count": replies,
                "listing_last_reply_at": parse_v2ex_time(ago.get("title") if ago else None),
                "source_priority": community in priority_nodes,
            }
        )
    return rows


def parse_detail(html: str, listing: dict[str, Any]) -> Story:
    soup = BeautifulSoup(html, "lxml")
    header = soup.select_one(".header")
    if not header:
        raise ValueError("schema_mismatch: V2EX topic header missing")

    posted_span = header.select_one("small.gray span[title]")
    posted_at = parse_v2ex_time(posted_span.get("title") if posted_span else None)

    reply_times = [
        parse_v2ex_time(span.get("title"))
        for span in soup.select(".cell[id^='r_'] span.ago[title]")
    ]
    reply_times = [value for value in reply_times if value]
    last_reply_at = max(reply_times, default=listing["listing_last_reply_at"] or posted_at)

    content = soup.select_one(".topic_content") or soup.select_one(".header .markdown_body")
    summary = content.get_text(" ", strip=True)[:500] if isinstance(content, Tag) else ""
    title_node = soup.select_one("h1")
    title = title_node.get_text(" ", strip=True) if title_node else listing["title"]

    return Story(
        platform="v2ex",
        community=listing["community"],
        title=title,
        summary=summary,
        url=listing["url"],
        posted_at=posted_at,
        last_reply_at=last_reply_at,
        reply_count=listing["reply_count"],
        source_priority=listing["source_priority"],
    )


class V2EXSource(SourceAdapter):
    name = "v2ex"

    def probe(self) -> SourceHealth:
        base_url = self.cfg["base_url"].rstrip("/")
        node = self.cfg.get("priority_nodes", ["invest"])[0]
        try:
            html = self.client.get_text(f"{base_url}/go/{node}")
            rows = parse_listing(html, base_url, node, {node})
            if not rows:
                raise ValueError("schema_mismatch: no V2EX topics found")
            return SourceHealth(self.name, True, candidates_seen=len(rows), reason=f"/go/{node} HTML")
        except Exception as exc:  # noqa: BLE001
            return SourceHealth(self.name, False, reason=str(exc))

    def collect(self, now: float) -> tuple[list[Story], SourceHealth]:
        base_url = self.cfg["base_url"].rstrip("/")
        priority = set(self.cfg.get("priority_nodes", []))
        secondary = list(self.cfg.get("secondary_nodes", []))
        min_replies = self._min_replies()
        listings_ok = 0
        failures: list[str] = []
        candidates: dict[str, dict[str, Any]] = {}

        sources: list[tuple[str, str, int]] = []
        for node in [*self.cfg.get("priority_nodes", []), *secondary]:
            sources.append((node, f"{base_url}/go/{node}", int(self.cfg.get("pages_per_node", 1))))
        if self.cfg.get("include_recent", True):
            sources.append(("recent", f"{base_url}/recent", int(self.cfg.get("recent_pages", 1))))

        for source_name, url, page_count in sources:
            for page in range(1, page_count + 1):
                page_url = url if page == 1 else f"{url}?p={page}"
                print(f"[v2ex] listing {source_name} p{page}/{page_count}", flush=True)
                try:
                    html = self.client.get_text(page_url)
                    parsed = parse_listing(html, base_url, source_name, priority)
                    listings_ok += 1
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{source_name}[{page}]:{exc}")
                    continue
                for row in parsed:
                    if row["reply_count"] < min_replies:
                        continue
                    if is_v2ex_excluded_ad(
                        row["title"],
                        community=row["community"],
                        cfg=self.cfg,
                    ):
                        continue
                    previous = candidates.get(row["url"])
                    if not previous or row["reply_count"] > previous["reply_count"]:
                        candidates[row["url"]] = row
                    elif row["source_priority"]:
                        previous["source_priority"] = True

        selected: list[Story] = []
        detail_failures = 0
        detail_total = len(candidates)
        for detail_i, row in enumerate(candidates.values(), start=1):
            if detail_i == 1 or detail_i % 5 == 0 or detail_i == detail_total:
                print(f"[v2ex] detail {detail_i}/{detail_total}", flush=True)
            try:
                story = parse_detail(self.client.get_text(row["url"]), row)
                if is_v2ex_excluded_ad(
                    story.title,
                    story.summary,
                    story.community,
                    self.cfg,
                ):
                    continue
                accepted, _ = evaluate(story, {"rules": self._rules()}, now)
                if accepted:
                    selected.append(story)
            except Exception:  # noqa: BLE001
                detail_failures += 1

        selected.sort(
            key=lambda story: (
                story.track == "long_tail",
                story.source_priority,
                story.reply_count,
                story.last_reply_at,
            ),
            reverse=True,
        )
        if not listings_ok:
            return [], SourceHealth(
                self.name,
                False,
                len(candidates),
                0,
                "; ".join(failures) or "all V2EX listing pages failed",
            )
        reason_parts = []
        if failures:
            reason_parts.append(f"partial listing failures={len(failures)}")
        if detail_failures:
            reason_parts.append(f"detail failures={detail_failures}")
        return selected, SourceHealth(
            self.name,
            True,
            len(candidates),
            len(selected),
            "; ".join(reason_parts),
        )

    def _rules(self) -> dict[str, Any]:
        return self.cfg["_global_rules"]

    def _min_replies(self) -> int:
        # "超过 N" is strict; keep candidates with replies >= N+1.
        return int(self._rules()["v2ex"]["hot_replies_strictly_over"]) + 1
