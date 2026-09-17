"""CLI for the Reddit + V2EX + Hacker News story radar."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from config import latest_dir, load_config, output_dir
from filtering import is_hn_tech_discussion, is_v2ex_excluded_ad, title_has_excluded_prefix
from http_client import PoliteClient
from models import SourceHealth, Story
from report import build_document, render_markdown
from sources import HackerNewsSource, RedditSource, V2EXSource
from translator import RedditTranslator, build_translation_client


def apply_saved_output_exclusions(
    document: dict[str, Any],
    cfg: dict[str, Any],
) -> None:
    prefixes = list(cfg.get("reddit", {}).get("exclude_title_prefixes", []))
    reddit = document.get("stories", {}).get("reddit", {})
    for track in ("long_tail", "recent"):
        reddit[track] = [
            row
            for row in reddit.get(track, [])
            if not title_has_excluded_prefix(str(row.get("title") or ""), prefixes)
        ]

    v2ex_cfg = cfg.get("v2ex", {})
    v2ex = document.get("stories", {}).get("v2ex", {})
    for track in ("long_tail", "recent"):
        v2ex[track] = [
            row
            for row in v2ex.get(track, [])
            if not is_v2ex_excluded_ad(
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                str(row.get("community") or ""),
                v2ex_cfg,
            )
        ]

    hn_cfg = cfg.get("hackernews", {})
    hn = document.get("stories", {}).get("hackernews", {})
    for track in ("long_tail", "recent"):
        hn[track] = [
            row
            for row in hn.get(track, [])
            if not is_hn_tech_discussion(
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                {**hn_cfg, "_board": str(row.get("community") or "")},
            )
        ]


def build_sources(
    cfg: dict[str, Any],
    client: PoliteClient,
    only: set[str] | None = None,
) -> list[Any]:
    sources = []
    for name, cls in (
        ("reddit", RedditSource),
        ("v2ex", V2EXSource),
        ("hackernews", HackerNewsSource),
    ):
        if only and name not in only:
            continue
        source_cfg = dict(cfg[name])
        if not source_cfg.get("enabled", True):
            continue
        source_cfg["_global_rules"] = cfg["rules"]
        source_cfg["_parent_cfg"] = cfg
        sources.append(cls(source_cfg, client))
    return sources


def collect(
    cfg: dict[str, Any],
    only: set[str] | None,
) -> tuple[dict[str, Any], str]:
    now = time.time()
    client = PoliteClient(cfg)
    stories: list[Story] = []
    health: list[SourceHealth] = []
    reddit_percentiles: dict[str, Any] | None = None
    sources = build_sources(cfg, client, only)
    for index, source in enumerate(sources, start=1):
        print(f"[collect] {index}/{len(sources)} {source.name} …", flush=True)
        try:
            selected, source_health = source.collect(now)
        except Exception as exc:  # noqa: BLE001
            selected = []
            source_health = SourceHealth(source.name, False, reason=str(exc))
        stories.extend(selected)
        health.append(source_health)
        print(
            f"[collect] {source.name} done ok={source_health.ok} "
            f"selected={len(selected)} seen={source_health.candidates_seen}",
            flush=True,
        )
        if source.name == "reddit" and getattr(source, "last_percentiles", None):
            reddit_percentiles = source.last_percentiles

    # The same V2EX topic can appear in its node and /recent.
    deduplicated: dict[str, Story] = {}
    for story in stories:
        previous = deduplicated.get(story.url)
        if not previous or story.reply_count > previous.reply_count:
            deduplicated[story.url] = story
    stories = list(deduplicated.values())

    translator = RedditTranslator(
        cfg.get("translation", {}),
        build_translation_client(cfg),
        output_dir(cfg),
    )
    translator.translate_all(stories)
    for platform_name in ("reddit", "hackernews"):
        platform_health = next((item for item in health if item.name == platform_name), None)
        if platform_health:
            translation_status = translator.status_text()
            platform_health.reason = "; ".join(
                part for part in (platform_health.reason, translation_status) if part
            )

    document = build_document(
        stories,
        health,
        now,
        cfg=cfg,
        reddit_percentiles=reddit_percentiles,
    )
    target_dir = latest_dir(cfg)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "hotspots.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    return document, path


def cmd_status(cfg: dict[str, Any], only: set[str] | None) -> None:
    client = PoliteClient(cfg)
    print(f"{'source':<12}{'ok':<7}{'candidates':<12}reason")
    print("-" * 100)
    for source in build_sources(cfg, client, only):
        health = source.probe()
        print(
            f"{health.name:<12}{str(health.ok):<7}"
            f"{health.candidates_seen:<12}{health.reason}"
        )


def cmd_collect(cfg: dict[str, Any], only: set[str] | None) -> None:
    document, path = collect(cfg, only)
    selected = sum(
        len(rows)
        for platform in document["stories"].values()
        for rows in platform.values()
    )
    print(f"[saved] {path} (selected stories: {selected})")


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write((text + "\n").encode(encoding, errors="replace"))
        else:
            print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def cmd_report(cfg: dict[str, Any]) -> None:
    target_dir = latest_dir(cfg)
    json_path = os.path.join(target_dir, "hotspots.json")
    if not os.path.exists(json_path):
        raise SystemExit("No hotspots.json. Run `python scripts/run_radar.py collect` first.")
    with open(json_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    apply_saved_output_exclusions(document, cfg)
    translator = RedditTranslator(
        cfg.get("translation", {}),
        build_translation_client(cfg),
        output_dir(cfg),
    )
    translator.translate_document(document)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    markdown = render_markdown(document)
    md_path = os.path.join(target_dir, "weekly_hotspots.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    _safe_print(markdown)
    print(f"[saved] {md_path}")


def cmd_all(cfg: dict[str, Any], only: set[str] | None) -> None:
    document, json_path = collect(cfg, only)
    markdown = render_markdown(document)
    md_path = os.path.join(latest_dir(cfg), "weekly_hotspots.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    _safe_print(markdown)
    print(f"[saved] {json_path}")
    print(f"[saved] {md_path}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="Reddit + V2EX + Hacker News story radar")
    parser.add_argument("command", choices=("status", "collect", "report", "all"))
    parser.add_argument(
        "--only",
        help="comma-separated sources: reddit,v2ex,hackernews",
    )
    args = parser.parse_args()
    only = {item.strip().lower() for item in args.only.split(",")} if args.only else None
    invalid = (only or set()) - {"reddit", "v2ex", "hackernews"}
    if invalid:
        parser.error(f"unknown sources: {', '.join(sorted(invalid))}")

    cfg = load_config()
    if args.command == "status":
        cmd_status(cfg, only)
    elif args.command == "collect":
        cmd_collect(cfg, only)
    elif args.command == "report":
        cmd_report(cfg)
    else:
        cmd_all(cfg, only)


if __name__ == "__main__":
    main()
