"""Story sources: the ``FeedSource`` abstraction and its implementations.

Each source fetches its own feed and yields a list of :class:`Post` — the lean,
source-agnostic contract the pipeline consumes (it merges Posts across sources
into the persisted :class:`~hackergist.models.Story` records).
"""

from __future__ import annotations

from .base import (
    FeedSource,
    Post,
    Source,
    SourceRegistry,
    assign_clout,
    min_max_clout,
    strip_html,
)
from .hackernews import HackerNewsSource
from .lobsters import LobstersSource

__all__ = [
    "FeedSource",
    "Post",
    "Source",
    "SourceRegistry",
    "assign_clout",
    "min_max_clout",
    "strip_html",
    "HackerNewsSource",
    "LobstersSource",
]
