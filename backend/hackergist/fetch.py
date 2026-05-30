"""Fetch + parse the two hnrss feeds, then union/dedupe into Stories.

hnrss item shape (per https://hnrss.org):

- ``<link>``    -> the ARTICLE url (or the HN item url for self-posts),
- ``<comments>``-> the HN discussion url (``item?id=<hn_id>``),
- ``<guid>``    -> the HN item url (carries the hn_id),
- ``<description>`` -> HTML with a "Points: N", "Comments: N" footer, and the
  self-text for Ask/Show posts.

We fetch both feeds concurrently, parse with feedparser, map each entry to a
:class:`~hackergist.models.Story`, and merge the two lists, deduping by
``hn_id`` (primary) and canonical url (secondary) while unioning ``feeds``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import feedparser
import httpx

from .config import Config
from .models import (
    Story,
    canonical_url,
    domain_of,
)

# Matches the HN item id in a comments / guid url: .../item?id=40000001
_HN_ID_RE = re.compile(r"[?&]id=(\d+)")
# Strips HTML tags when pulling self-text out of a description blob.
_TAG_RE = re.compile(r"<[^>]+>")
# hnrss footer lines we don't want as gist input.
_POINTS_RE = re.compile(r"Points:\s*(\d+)", re.IGNORECASE)


def fetch_feed(client: httpx.Client, url: str, config: Config) -> bytes | None:
    """Fetch one feed's raw bytes, with retries. Returns ``None`` on failure."""
    for _attempt in range(config.http_max_retries + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.content
        except (httpx.HTTPError, httpx.TransportError):  # network/HTTP
            continue
    # All attempts failed; swallow and let the caller treat the feed as empty.
    return None


def fetch_feeds(config: Config) -> dict[str, list[Story]]:
    """Fetch BOTH feeds concurrently and parse each into a list of Stories.

    Returns a map of feed name -> stories. A feed that fails to fetch or parse
    yields an empty list (so a single dead feed never sinks the run).
    """
    headers = {"User-Agent": config.user_agent, "Accept": "application/rss+xml, application/xml"}
    timeout = httpx.Timeout(config.http_timeout_seconds)
    results: dict[str, list[Story]] = {}

    with (
        httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = {
            name: pool.submit(fetch_feed, client, url, config)
            for name, url in config.feed_urls.items()
        }
        for name, future in futures.items():
            raw = future.result()
            results[name] = parse_feed(raw, name) if raw else []

    return results


def parse_feed(raw: bytes | str, feed_name: str) -> list[Story]:
    """Parse one feed's bytes into Stories tagged with ``feed_name``."""
    parsed = feedparser.parse(raw)
    stories: list[Story] = []
    for entry in parsed.entries:
        story = _entry_to_story(entry, feed_name)
        if story is not None:
            stories.append(story)
    return stories


def _entry_to_story(entry: Any, feed_name: str) -> Story | None:
    """Map a single feedparser entry to a Story, or ``None`` if unusable."""
    comments_url = _get(entry, "comments") or _get(entry, "guid") or _get(entry, "id")
    hn_id = _extract_hn_id(comments_url) or _extract_hn_id(_get(entry, "id"))
    if hn_id is None:
        return None

    # Normalize comments url to the canonical HN item permalink.
    canonical_comments = f"https://news.ycombinator.com/item?id={hn_id}"

    article_url = _get(entry, "link") or None
    title = (_get(entry, "title") or "").strip() or "(untitled)"

    # Ask/Show/text posts: the "article" link IS the HN item itself -> no
    # external link. Detect by comparing to the HN item permalink.
    hn_text: str | None = None
    if article_url and _extract_hn_id(article_url) == hn_id:
        hn_text = _extract_self_text(_get(entry, "summary") or _get(entry, "description"))
        article_url = None

    domain = domain_of(article_url)
    points = _extract_points(entry)
    author = (_get(entry, "author") or "").strip() or None
    published = _parse_published(entry)

    return Story(
        hn_id=hn_id,
        title=title,
        url=article_url,
        domain=domain,
        comments_url=canonical_comments,
        points=points,
        author=author,
        published=published,
        feeds=[feed_name],
        hn_text=hn_text,
    )


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
            existing_id = by_id.get(incoming.hn_id)
            if existing_id is None:
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
    from .models import normalize_feeds

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


# --- entry-field helpers ------------------------------------------------------


def _get(entry: Any, key: str) -> str | None:
    """Read a feedparser field whether it's dict- or attribute-shaped."""
    value = entry.get(key) if isinstance(entry, dict) else getattr(entry, key, None)
    return value if isinstance(value, str) else None


def _extract_hn_id(url: str | None) -> int | None:
    """Pull the integer HN item id out of an ``item?id=...`` style url."""
    if not url:
        return None
    match = _HN_ID_RE.search(url)
    if match:
        return int(match.group(1))
    # Fallback: parse the query string for an ``id`` param.
    query = parse_qs(urlsplit(url).query)
    if "id" in query and query["id"]:
        try:
            return int(query["id"][0])
        except ValueError:
            return None
    return None


def _extract_points(entry: Any) -> int | None:
    """Read points from hnrss; falls back to the description footer."""
    # hnrss exposes a custom ``points`` element; feedparser surfaces it as a
    # namespaced key. Try the obvious spots first.
    for key in ("hnrss_points", "points"):
        value = _get(entry, key)
        if value and value.isdigit():
            return int(value)
    blob = _get(entry, "summary") or _get(entry, "description") or ""
    match = _POINTS_RE.search(blob)
    if match:
        return int(match.group(1))
    return None


def _parse_published(entry: Any) -> str | None:
    """Return the submit time as an ISO-8601 UTC string, or ``None``."""
    # Prefer feedparser's parsed struct_time when available.
    struct = None
    if isinstance(entry, dict):
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
    else:
        struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct is not None:
        try:
            dt = datetime(*struct[:6], tzinfo=UTC)
            return _iso_z(dt)
        except (TypeError, ValueError):
            pass

    raw = _get(entry, "published") or _get(entry, "updated")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return _iso_z(dt)
        except (TypeError, ValueError, IndexError):
            return None
    return None


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _extract_self_text(description: str | None) -> str | None:
    """Pull the self-post body out of an hnrss description blob.

    Strips HTML tags and the trailing "Points:/Comments:" footer so the gist
    model sees clean prose.
    """
    if not description:
        return None
    text = _TAG_RE.sub(" ", description)
    # Drop the hnrss metadata footer lines.
    text = re.split(r"(?:Article URL|Comments URL|Points|# Comments):", text)[0]
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
