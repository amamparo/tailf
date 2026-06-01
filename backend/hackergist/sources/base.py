"""The ``FeedSource`` interface + the ``Post`` it produces, and clout scoring.

A ``FeedSource`` is an interchangeable producer of ``Post``s. The pipeline calls
``get_posts()`` on each enabled source, then merges the results across sources
(by canonical URL) into the persisted ``Story`` records.

``Post`` is deliberately lean — title, link, comments URL, and a few fields the
pipeline needs (``source``/``published``/``points``/``self_text``). Each source
also assigns ``clout``: a 0..1 min-max normalization of ``points`` over THAT
source's own batch (1.0 = the highest-pointed post returned, 0.0 = the lowest).
clout has NO time component — it is strictly a function of points; the feed sort
is a separate concern that combines clout with the post's timestamp.
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
    #: 0..1 min-max of ``points`` within this source's batch; assigned by the
    #: source after the whole batch is fetched (see :func:`assign_clout`).
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


def min_max_clout(points: list[int | None]) -> list[float]:
    """Min-max normalize ``points`` to 0..1, one clout per input (order-aligned).

    Rules (the edge cases):

    - All points equal, or a single post (``max == min``): every present score
      maps to ``1.0`` — they're all equally "top of the batch".
    - A ``None`` point (no community score): maps to ``0.0`` (the floor).
    - No numeric points at all: everything is ``0.0``.
    """
    nums = [p for p in points if isinstance(p, int | float)]
    if not nums:
        return [0.0 for _ in points]
    lo, hi = min(nums), max(nums)
    if hi == lo:
        return [1.0 if isinstance(p, int | float) else 0.0 for p in points]
    span = hi - lo
    return [(p - lo) / span if isinstance(p, int | float) else 0.0 for p in points]


def assign_clout(posts: list[Post]) -> list[Post]:
    """Set each post's ``clout`` from the batch's points (in place); return them."""
    for post, clout in zip(posts, min_max_clout([p.points for p in posts]), strict=True):
        post.clout = clout
    return posts
