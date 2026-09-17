"""Configuration helpers."""

from __future__ import annotations

import json
import os
from typing import Any

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(SKILL_ROOT, "config.json")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def output_dir(cfg: dict[str, Any]) -> str:
    configured = cfg.get("output", {}).get("dir", "output")
    if os.path.isabs(configured):
        return configured
    return os.path.join(SKILL_ROOT, configured)


def latest_dir(cfg: dict[str, Any]) -> str:
    return os.path.join(
        output_dir(cfg),
        cfg.get("output", {}).get("latest_dir", "latest"),
    )
