"""Source adapter contract."""

from __future__ import annotations

from typing import Any

from http_client import PoliteClient
from models import SourceHealth, Story


class SourceAdapter:
    name = ""

    def __init__(self, cfg: dict[str, Any], client: PoliteClient):
        self.cfg = cfg
        self.client = client

    def collect(self, now: float) -> tuple[list[Story], SourceHealth]:
        raise NotImplementedError
