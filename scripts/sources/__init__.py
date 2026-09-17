"""Source adapters."""

from sources.hackernews import HackerNewsSource
from sources.reddit import RedditSource
from sources.v2ex import V2EXSource

__all__ = ["HackerNewsSource", "RedditSource", "V2EXSource"]
