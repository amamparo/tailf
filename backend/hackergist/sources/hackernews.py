"""Hacker News source — the official Firebase API → ``Post``s.

We use Hacker News' OWN API (https://github.com/HackerNews/API) — the canonical
source (no key, no documented rate limit) that hnrss merely scrapes. Two
id-lists feed the union: ``topstories`` (front-page set) and ``beststories``
(points-ranked). We take the first ``*_limit`` of each, hydrate the union of ids
once via ``item/<id>.json`` (concurrently), map each to a :class:`Post`, dedupe
within the source by canonical URL (max points wins), and assign clout over the
batch.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import Config
from ..models import canonical_url, iso_from_epoch
from .base import FeedSource, Post, assign_clout, strip_html

logger = logging.getLogger(__name__)

# HN item types that carry a gistable title and belong in these lists. Comments
# / pollopts shouldn't appear in top/best, but we guard anyway.
_KEEP_TYPES = frozenset({"story", "job", "poll"})


class HackerNewsSource(FeedSource):
    """Fetches HN topstories + beststories and yields deduped ``Post``s."""

    name = "hn"

    def __init__(self, config: Config) -> None:
        self.config = config

    def get_posts(self) -> list[Post]:
        return asyncio.run(self._get_posts_async())

    async def _get_posts_async(self, client: httpx.AsyncClient | None = None) -> list[Post]:
        config = self.config
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                headers={"User-Agent": config.user_agent, "Accept": "application/json"},
                timeout=httpx.Timeout(config.http_timeout_seconds),
                follow_redirects=True,
            )
        try:
            # 1. Fetch the two id-lists concurrently, cap each to its limit.
            lists = ((config.top_url, config.top_limit), (config.best_url, config.best_limit))
            id_lists = await asyncio.gather(
                *(_fetch_id_list(client, url, config) for url, _limit in lists)
            )

            # 2. Union of ids, first-appearance order (topstories first).
            order: list[int] = []
            seen: set[int] = set()
            for ids, (_url, limit) in zip(id_lists, lists, strict=True):
                for hn_id in ids[:limit]:
                    if hn_id not in seen:
                        seen.add(hn_id)
                        order.append(hn_id)

            # 3. Hydrate the union once, concurrently.
            items = await _fetch_items(client, order, config)

            # 4. Map to Posts, then dedupe by canonical URL (max points wins).
            posts: list[Post] = []
            for hn_id in order:
                post = _item_to_post(items.get(hn_id))
                if post is not None:
                    posts.append(post)
            posts = _dedupe_by_url(posts)

            if not posts:
                logger.warning("HN source returned 0 posts this run")
            else:
                logger.info("HN source: %d posts", len(posts))
            return assign_clout(posts)
        finally:
            if owns_client:
                await client.aclose()


async def _fetch_id_list(client: httpx.AsyncClient, url: str, config: Config) -> list[int]:
    """GET an id-list endpoint; return the list of int ids, or ``[]`` on failure."""
    data = await _get_json(client, url, config)
    if not isinstance(data, list):
        if data is None:
            logger.warning("id-list fetch failed: %s", url)
        return []
    return [int(x) for x in data if isinstance(x, int)]


async def _fetch_items(
    client: httpx.AsyncClient, ids: list[int], config: Config
) -> dict[int, dict]:
    """Concurrently hydrate ``item/<id>.json`` for each id (semaphore-capped)."""
    if not ids:
        return {}
    semaphore = asyncio.Semaphore(max(1, min(config.item_fetch_concurrency, len(ids))))

    async def fetch_one(hn_id: int) -> tuple[int, object]:
        url = config.item_url_template.format(id=hn_id)
        async with semaphore:
            return hn_id, await _get_json(client, url, config)

    pairs = await asyncio.gather(*(fetch_one(i) for i in ids))
    return {hn_id: data for hn_id, data in pairs if isinstance(data, dict)}


async def _get_json(client: httpx.AsyncClient, url: str, config: Config) -> object:
    """GET + parse JSON with retries. Returns the parsed value, or ``None``."""
    for attempt in range(config.http_max_retries + 1):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):  # network / HTTP status / JSON decode
            if attempt == config.http_max_retries:
                logger.warning("GET failed after %d attempt(s): %s", attempt + 1, url)
    return None


def _item_to_post(item: object) -> Post | None:
    """Map one HN Firebase item dict to a :class:`Post`, or ``None`` if unusable.

    Drops deleted/dead items and non-story types. Ask/Show/text/poll items have
    no external ``url`` (-> ``link=None``); their HTML ``text`` is captured (tags
    stripped) as ``self_text`` gist input.
    """
    if not isinstance(item, dict):
        return None
    if item.get("deleted") or item.get("dead"):
        return None
    hn_id = item.get("id")
    if not isinstance(hn_id, int):
        return None
    if item.get("type") not in _KEEP_TYPES:
        return None

    title = (item.get("title") or "").strip() or "(untitled)"
    score = item.get("score")
    return Post(
        title=title,
        link=item.get("url") or None,
        comments_url=f"https://news.ycombinator.com/item?id={hn_id}",
        source="hn",
        published=iso_from_epoch(item.get("time")),
        points=score if isinstance(score, int) else None,
        self_text=strip_html(item.get("text")),
    )


def _dedupe_by_url(posts: list[Post]) -> list[Post]:
    """Collapse posts that share a canonical link URL, keeping the max-points one.

    Self/text posts (``link is None``) are never merged. First-appearance order
    is preserved for determinism.
    """
    by_url: dict[str, int] = {}  # canonical url -> index in `out`
    out: list[Post] = []
    for post in posts:
        curl = canonical_url(post.link) if post.link else None
        if curl is not None and curl in by_url:
            kept = out[by_url[curl]]
            if (post.points or -1) > (kept.points or -1):
                kept.points = post.points
            continue
        if curl is not None:
            by_url[curl] = len(out)
        out.append(post)
    return out
