# Investment Community Radar

[中文版](README.zh-CN.md)

Read-only radar that pulls high-reply **hot news** and **long-tail stories** from personal-investing communities on **Reddit**, selected **V2EX** nodes, and **Hacker News** (`/front`, Ask HN, Show HN). It produces a Chinese-friendly material list with original post links—useful for behavioral-finance writing, topic research, and cross-community story hunting.

It does **not** cluster events across sites, map psychology labels automatically, or require the same topic to appear on multiple platforms.

## Features

- Three sources: Reddit (8 investing subreddits), V2EX (invest/stock/bitcoin + related nodes), Hacker News (front / ask / show)
- Dual tracks: recent hot posts vs long-tail high-reply stories
- Reddit thresholds use per-subreddit all-time comment **P95** (with floor values), cached with a configurable TTL
- Optional no-key English → Chinese titles/summaries for Reddit and HN (can be disabled)
- Structured JSON + Markdown report; failures are recorded as coverage gaps (no silent backfill from memory)

## Requirements

- Python 3.10+
- Network access to the public endpoints used by each source

```bash
pip install -r requirements.txt
```

Dependencies: `requests`, `beautifulsoup4`, `lxml`.

## Quick start

Run from the repository root:

```bash
python scripts/run_radar.py status
python scripts/run_radar.py collect
python scripts/run_radar.py all
```

| Command | What it does |
| --- | --- |
| `status` | Probe each source (fix network/proxy issues before collecting) |
| `collect` | Write `output/latest/hotspots.json` only |
| `report` | Render Markdown from the latest JSON (and backfill Chinese fields when possible) |
| `all` | `collect` + generate/print `weekly_hotspots.md` |

Filter by platform:

```bash
python scripts/run_radar.py collect --only reddit
python scripts/run_radar.py all --only v2ex
python scripts/run_radar.py all --only hackernews
python scripts/run_radar.py all --only reddit,hackernews
```

### Outputs

- `output/latest/hotspots.json` — structured results
- `output/latest/weekly_hotspots.md` — Chinese reading view (`all` / `report`)
- `output/reddit_percentiles.json` — Reddit P95 cache
- `output/translation_cache.json` — optional translation cache (content hashes only)

## Sources

### Reddit

- `r/wallstreetbets`, `r/personalfinance`, `r/investing`, `r/Bogleheads`
- `r/financialindependence`, `r/ValueInvesting`, `r/stocks`, `r/dividends`

Daily FI discussion threads (title prefix) are excluded. Official public JSON is tried first; on widespread `403`, the collector falls back to the **Arctic Shift** public archive API (read-only archive, not a proxy/bypass).

### V2EX

Priority nodes: `/go/invest`, `/go/stock`, `/go/bitcoin`.  
Also scans programmer / business / career / life-related nodes and `/recent`. Brokerage ads and AI relay / API-key promos are filtered out.

### Hacker News

- `/front` (daily lookback ~1 month; external links get a page snippet in `summary`)
- Ask HN / Show HN via Algolia

Pure tech/engineering threads are filtered unless the title or summary carries an investing / decision signal.

## Selection rules (summary)

| Platform | Hot (recent) | Long-tail |
| --- | --- | --- |
| Reddit | Posted in last **7** calendar days, comments **>** `max(50, subreddit P95)` | Comments **>** `max(100, subreddit P95)`, any date |
| V2EX | Last **7** days, replies **>** 100 | Replies **>** 1000, any date |
| Hacker News | Last **30** days, replies **>** 100 | Last **30** days, replies **>** 1000 |

If both tracks match, the item is classified as long-tail. Thresholds and lookbacks are configurable in `config.json`.

Reddit P95 is refreshed with stratified yearly sampling and cached (default TTL **24 hours** via `reddit.percentile_cache_ttl_hours`). On refresh failure the cache is reused; otherwise only the floor values apply.

## Configuration

Main knobs live in [`config.json`](config.json):

- `request.*` — timeout, polite interval, retries
- `rules.*` — reply floors / lookback windows per platform
- `reddit` / `v2ex` / `hackernews` — boards, pages, filters, archive settings
- `translation.enabled` — turn Chinese title/summary generation on or off

Field glossary: [`references/fields.md`](references/fields.md)  
Source notes: [`references/sources.md`](references/sources.md)

## Project layout

```
├── SKILL.md                 # Cursor Agent Skill description (Chinese-first)
├── config.json
├── requirements.txt
├── scripts/
│   ├── run_radar.py         # CLI entry
│   ├── filtering.py
│   ├── translator.py
│   ├── report.py
│   └── sources/             # reddit / v2ex / hackernews adapters
├── references/
├── tests/
└── output/                  # generated locally (usually not committed)
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Safety & ethics

- Read-only, low-frequency, serial requests; no captcha breaking or blockade bypass
- No cookies, username lists, or full comment threads are stored
- Reports keep public title/summary snippets, counts, timestamps, and links
- Optional translation sends public Reddit/HN title + summary text to a no-key translate endpoint; disable in `config.json` if unwanted

## License

No license file is bundled yet. Add one before publishing if you want others to reuse the code under clear terms.

## Cursor Agent Skill

This repo doubles as a [Cursor](https://cursor.com) Agent Skill (`SKILL.md`). Place or link it under your skills directory and invoke it when you need investing-community story material from Reddit / V2EX / HN.
