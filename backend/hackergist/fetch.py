"""Fetch HN stories from the official Firebase API, then union/dedupe into Stories.

We use Hacker News' OWN API (https://github.com/HackerNews/API) — the canonical
source (no key, no documented rate limit) that hnrss merely scrapes — rather
than a third-party RSS proxy. Two id-lists drive the "two feeds" we union:

- ``topstories.json``  -> the front-page set       (feed name ``frontpage``),
- ``beststories.json`` -> the points-ranked "best"  (feed name ``best``).

Each is a JSON array of HN item ids. We take the first ``*_limit`` of each,
hydrate the *union* of ids once via ``item/<id>.json`` (concurrently), map each
item to a :class:`~hackergist.models.Story` tagged with the feed(s) it appeared
in, and return per-feed lists for :func:`union_feeds` to dedupe (by hn_id, then
canonical url) exactly as before — so the data.json contract is unchanged.

This replaced the hnrss RSS feeds, whose /best endpoint (a fragile HN-HTML
scrape behind a 55-min cache) routinely returned 502s or empty 200s, collapsing
the union and pruning the feed.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

import httpx

from .config import Config
from .models import (
    Story,
    canonical_url,
    domain_of,
    iso_from_epoch,
    normalize_feeds,
)

logger = logging.getLogger(__name__)

# Strips HTML tags from an HN self-post ``text`` blob (it's HTML, with entities).
_TAG_RE = re.compile(r"<[^>]+>")

# HN item types that carry a gistable title and belong in these lists. Comments
# / pollopts shouldn't appear in top/best, but we guard anyway.
_KEEP_TYPES = frozenset({"story", "job", "poll"})


def fetch_feeds(config: Config) -> dict[str, list[Story]]:
    """Fetch both id-lists, hydrate their items, and return feed name -> Stories.

    A list or item fetch that fails yields fewer (or zero) stories for that feed
    rather than raising — a single upstream hiccup never sinks the run. The
    per-feed counts are logged (a 0-count feed is a WARNING) for observability.
    """
    return asyncio.run(_fetch_feeds_async(config))


async def _fetch_feeds_async(
    config: Config, client: httpx.AsyncClient | None = None
) -> dict[str, list[Story]]:
    """Async core of :func:`fetch_feeds`; accepts a client for testing."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            headers={"User-Agent": config.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(config.http_timeout_seconds),
            follow_redirects=True,
        )
    try:
        sources = config.id_list_sources  # name -> (url, limit)
        names = list(sources)

        # 1. Fetch the id-lists concurrently, then cap each to its limit.
        id_lists = await asyncio.gather(
            *(_fetch_id_list(client, url, config) for url, _limit in sources.values())
        )
        feed_ids = {
            name: ids[:limit]
            for name, ids, (_url, limit) in zip(names, id_lists, sources.values(), strict=True)
        }

        # 2. Build feed membership per id, preserving order (frontpage first).
        feeds_of: dict[int, list[str]] = {}
        order: list[int] = []
        for name in names:
            for hn_id in feed_ids[name]:
                if hn_id not in feeds_of:
                    feeds_of[hn_id] = []
                    order.append(hn_id)
                feeds_of[hn_id].append(name)

        # 3. Hydrate the union of ids once, concurrently.
        items = await _fetch_items(client, order, config)  # id -> dict | None

        # 4. Map to Stories and bucket into per-feed lists (each story carries
        #    its full feed membership; union_feeds dedupes the overlap).
        result: dict[str, list[Story]] = {name: [] for name in names}
        for hn_id in order:
            story = _item_to_story(items.get(hn_id), feeds_of[hn_id])
            if story is None:
                continue
            for name in story.feeds:
                if name in result:
                    result[name].append(story)

        for name in names:
            count = len(result[name])
            if count == 0:
                logger.warning("feed %r returned 0 stories this run", name)
            else:
                logger.info("feed %r: %d stories", name, count)
        return result
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
    """GET + parse JSON with retries. Returns the parsed value, or ``None``.

    Retries on any HTTP/transport error AND on a JSON decode failure (a partial
    or non-JSON body), so a transient hiccup doesn't drop a feed or item.
    """
    for attempt in range(config.http_max_retries + 1):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):  # network / HTTP status / JSON decode
            if attempt == config.http_max_retries:
                logger.warning("GET failed after %d attempt(s): %s", attempt + 1, url)
    return None


def _item_to_story(item: object, feeds: list[str]) -> Story | None:
    """Map one HN Firebase item dict to a Story, or ``None`` if unusable.

    Drops deleted/dead items and non-story types. Ask/Show/text/poll items have
    no external ``url`` (-> ``None``, which the gist path handles as hn_text);
    their HTML ``text`` is captured (tags stripped) as gist input.
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
    url = item.get("url") or None
    score = item.get("score")
    points = score if isinstance(score, int) else None

    return Story(
        hn_id=hn_id,
        title=title,
        url=url,
        domain=domain_of(url),
        comments_url=f"https://news.ycombinator.com/item?id={hn_id}",
        points=points,
        author=(item.get("by") or "").strip() or None,
        published=iso_from_epoch(item.get("time")),
        feeds=list(feeds),
        hn_text=_strip_html(item.get("text")),
    )


def _strip_html(text: object) -> str | None:
    """Strip tags + unescape entities from an HN self-post HTML ``text`` blob."""
    if not isinstance(text, str) or not text:
        return None
    cleaned = html.unescape(_TAG_RE.sub(" ", text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def union_feeds(feeds: dict[str, list[Story]]) -> list[Story]:
    """Merge per-feed story lists, deduping by hn_id and canonical url.

    When the same story appears in both feeds, the feed names are unioned and
    the richer (non-null) field values win. Order of first appearance is
    preserved for determinism.
    """
    by_id: dict[int, Story] = {}
    by_url: dict[str, int] = {}
    order: list[int] = []

    for stories in feeds.values():
        for incoming in stories:
            existing = by_id.get(incoming.hn_id)
            if existing is None:
                # Secondary dedupe: same canonical url under a different id.
                curl = canonical_url(incoming.url)
                if curl is not None and curl in by_url:
                    _merge_into(by_id[by_url[curl]], incoming)
                    continue

            target = by_id.get(incoming.hn_id)
            if target is None:
                by_id[incoming.hn_id] = incoming
                order.append(incoming.hn_id)
                curl = canonical_url(incoming.url)
                if curl is not None:
                    by_url.setdefault(curl, incoming.hn_id)
            else:
                _merge_into(target, incoming)

    return [by_id[hn_id] for hn_id in order]


def _merge_into(target: Story, incoming: Story) -> None:
    """Fold ``incoming`` into ``target`` in place (union feeds, fill gaps)."""
    target.feeds = normalize_feeds([*target.feeds, *incoming.feeds])
    if target.url is None and incoming.url is not None:
        target.url = incoming.url
        target.domain = incoming.domain
    if target.points is None and incoming.points is not None:
        target.points = incoming.points
    if target.author is None and incoming.author is not None:
        target.author = incoming.author
    if target.published is None and incoming.published is not None:
        target.published = incoming.published
    if target.hn_text is None and incoming.hn_text is not None:
        target.hn_text = incoming.hn_text
