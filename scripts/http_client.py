"""Small polite HTTP client with classified failures."""

from __future__ import annotations

import json
import time
from typing import Any

import requests


class FetchError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


class PoliteClient:
    def __init__(self, cfg: dict[str, Any]):
        request_cfg = cfg.get("request", {})
        self.timeout = float(request_cfg.get("timeout_seconds", 20))
        self.min_interval = float(request_cfg.get("min_interval_seconds", 1.0))
        self.max_retries = int(request_cfg.get("max_retries", 2))
        self.backoff = float(request_cfg.get("backoff_seconds", 2.0))
        self.session = requests.Session()
        # Translation client sets trust_env=false so a dead local proxy cannot hang GETs.
        if "trust_env" in request_cfg:
            self.session.trust_env = bool(request_cfg.get("trust_env"))
        proxies = request_cfg.get("proxies")
        if proxies is None and request_cfg.get("proxy"):
            proxy_url = str(request_cfg["proxy"]).strip()
            if proxy_url:
                proxies = {"http": proxy_url, "https": proxy_url}
        if isinstance(proxies, dict):
            self.session.proxies.update(proxies)
        self.session.headers.update(
            {
                "User-Agent": request_cfg.get("user_agent", "investment-community-radar/2.0"),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self._last_request = 0.0

    def _wait(self) -> None:
        remaining = self.min_interval - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        timeout = float(kwargs.pop("timeout", self.timeout))
        for attempt in range(self.max_retries + 1):
            self._wait()
            try:
                response = self.session.get(url, timeout=timeout, **kwargs)
                self._last_request = time.monotonic()
            except requests.Timeout as exc:
                last_error = FetchError("timeout", str(exc))
            except requests.RequestException as exc:
                last_error = FetchError("network", str(exc))
            else:
                if response.status_code == 403:
                    raise FetchError("http_403", f"HTTP 403 from {url}")
                if response.status_code == 429:
                    last_error = FetchError("http_429", f"HTTP 429 from {url}")
                elif 400 <= response.status_code < 500:
                    raise FetchError(
                        "http_4xx", f"HTTP {response.status_code} from {url}"
                    )
                elif response.status_code >= 500:
                    last_error = FetchError(
                        "http_5xx", f"HTTP {response.status_code} from {url}"
                    )
                else:
                    return response

            if attempt < self.max_retries:
                time.sleep(self.backoff * (attempt + 1))
        if isinstance(last_error, Exception):
            raise last_error
        raise FetchError("network", f"request failed: {url}")

    def get_text(self, url: str, **kwargs: Any) -> str:
        response = self.get(url, **kwargs)
        if not response.text.strip():
            raise FetchError("empty_body", f"empty response from {url}")
        return response.text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.get(url, **kwargs)
        text = response.text.lstrip()
        if text.startswith("<") or "text/html" in response.headers.get("content-type", ""):
            raise FetchError("decode", f"HTML returned instead of JSON from {url}")
        try:
            return response.json()
        except (requests.JSONDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise FetchError("decode", f"invalid JSON from {url}: {exc}") from exc
