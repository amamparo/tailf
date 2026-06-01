"""The ``FeedSource`` interface + the ``Post`` it produces, and clout scoring.

A ``FeedSource`` is an interchangeable producer of ``Post``s. The pipeline calls
``get_posts()`` on each enabled source, then merges the results across sources
(by canonical URL) into the persisted ``Story`` records.

``Post`` is deliberately lean — title, link, comments URL, and a few fields the
pipeline needs (``source``/``published``/``points``/``self_text``). Each source
also assigns ``clout``: a 0..1 PERCENTILE-RANK of ``points`` within THAT source's
own batch (1.0 = the top-pointed post returned, 0.0 = the lowest). Rank (not
min-max) so every source has the same clout distribution — the key to
interleaving sources evenly regardless of their point scales. clout has NO time
component — it is strictly a function of points (the feed sort folds in recency
separately).
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

#: Which community a post came from. Mirrored (as a string) in the data.json
#: contract via ``Discussion.source``.
Source = Literal["hn", "lobsters"]

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: object) -> str | None:
    """Strip tags + unescape entities from a self-post HTML body, or ``None``."""
    if not isinstance(text, str) or not text:
        return None
    cleaned = html.unescape(_TAG_RE.sub(" ", text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


@dataclass
class Post:
    """One story as surfaced by a single source, before pipeline enrichment."""

    title: str
    #: Article URL. ``None`` for self/text posts (HN Ask/Show, lobste.rs ``ask``).
    link: str | None
    #: The discussion permalink (HN ``item?id=``; lobste.rs ``/s/{short_id}``).
    comments_url: str
    source: Source
    #: ISO-8601 UTC submit time, or ``None`` if the feed entry had no usable date.
    published: str | None
    #: Raw community score. Drives ``clout`` and the interim sort; ``None`` when
    #: the source didn't expose one (e.g. an HN job post).
    points: int | None
    #: Body of a self/text post, used as gist input when there is no ``link``.
    self_text: str | None = None
    #: 0..1 percentile-rank of ``points`` within this source's batch; assigned by
    #: the source after the whole batch is fetched (see :func:`assign_clout`).
    clout: float = 0.0


class FeedSource(ABC):
    """A source of stories. Implementations fetch a feed and return ``Post``s."""

    #: Stable source tag — also the ``discussions[].source`` value and badge key.
    name: Source

    @abstractmethod
    def get_posts(self) -> list[Post]:
        """Fetch this source's current feed as a list of ``Post`` (clout set)."""
        raise NotImplementedError


@dataclass
class SourceRegistry:
    """The enabled sources for a run.

    A thin wrapper so the DI container can bind the source list (``injector``
    has no first-class multibinding for a bare ``list[FeedSource]``).
    """

    sources: list[FeedSource]


def rank_clout(points: list[int | None]) -> list[float]:
    """Percentile-RANK each score within the batch to 0..1 (order-aligned).

    This is a rank normalization, deliberately NOT min-max. A post's clout is its
    position in its source's own point distribution, which is robust to outliers
    and batch size and — crucially — gives every source the SAME clout
    distribution. That is what lets the merged feed interleave sources evenly: a
    source's absolute point scale (HN's hundreds vs lobste.rs's tens) no longer
    decides who sits on top. Min-max failed here: one high-point outlier pinned
    1.0 and crushed the rest of that source toward 0.

    Rules: ties share the average rank; a single (or empty) batch of real scores
    is ``1.0``; a ``None`` score (no community score) floors to ``0.0``.
    """
    present = [p for p in points if isinstance(p, int | float)]
    n = len(present)
    if n <= 1:
        return [1.0 if isinstance(p, int | float) else 0.0 for p in points]

    def pct(value: float) -> float:
        below = sum(1 for q in present if q < value)
        equal = sum(1 for q in present if q == value)
        return (below + (equal - 1) / 2) / (n - 1)

    return [pct(p) if isinstance(p, int | float) else 0.0 for p in points]


def assign_clout(posts: list[Post]) -> list[Post]:
    """Set each post's ``clout`` from the batch's points (in place); return them."""
    for post, clout in zip(posts, rank_clout([p.points for p in posts]), strict=True):
        post.clout = clout
    return posts
