"""lobste.rs source — the public ``rss.rss`` feed + a ``hottest.json`` scrape.

The RSS feed drives the post LIST (title, link, comments URL, time, tags), but
it carries no score — so a separate scrape of ``hottest.json`` (the same hottest
ordering, fetched non-atomically) supplies ``points`` for clout. The two are
joined by lobste.rs ``short_id``; a per-story ``/s/{short_id}.json`` fetch fills
any boundary skew (an RSS entry that has dropped out of hottest by the time we
ask). Self/text posts (``ask``/``show``, or a link that points back at the
lobste.rs thread) carry no external article — their body becomes ``self_text``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

import feedparser
import httpx

from ..config import Config
from ..models import to_iso_z
from .base import FeedSource, Post, assign_clout, strip_html

logger = logging.getLogger(__name__)

_SELF_POST_TAGS = frozenset({"ask", "show"})


class LobstersSource(FeedSource):
    """Fetches lobste.rs hottest (via RSS + a points scrape) as ``Post``s."""

    name = "lobsters"

    def __init__(self, config: Config) -> None:
        self.config = config

    def get_posts(self) -> list[Post]:
        cfg = self.config
        ua = cfg.lobsters_user_agent or cfg.user_agent
        with httpx.Client(
            headers={"User-Agent": ua},
            timeout=httpx.Timeout(cfg.http_timeout_seconds),
            follow_redirects=True,
        ) as client:
            return self._collect(client)

    def _collect(self, client: httpx.Client) -> list[Post]:
        """Core of :meth:`get_posts`; takes a client so tests can mock it."""
        items = self._fetch_rss(client)  # list[(short_id, Post)]
        if not items:
            logger.warning("lobste.rs source returned 0 posts this run")
            return []
        scores = self._fetch_scores(client, [sid for sid, _ in items])
        posts: list[Post] = []
        for short_id, post in items:
            post.points = scores.get(short_id)
            posts.append(post)
        logger.info("lobste.rs source: %d posts", len(posts))
        return assign_clout(posts)

    def _fetch_rss(self, client: httpx.Client) -> list[tuple[str, Post]]:
        text = self._get_text(client, self.config.lobsters_rss_url)
        if not text:
            return []
        items: list[tuple[str, Post]] = []
        for entry in feedparser.parse(text).entries:
            short_id = _short_id(entry.get("id") or entry.get("guid"))
            if not short_id:
                continue
            comments_url = entry.get("comments") or entry.get("id")
            if not comments_url:
                continue
            link = entry.get("link") or None
            tags = {str(t.get("term", "")).lower() for t in entry.get("tags", [])}
            self_text = None
            if _is_self_post(link, short_id, tags):
                self_text = strip_html(entry.get("summary"))
                link = None
            post = Post(
                title=(entry.get("title") or "").strip() or "(untitled)",
                link=link,
                comments_url=comments_url,
                source="lobsters",
                published=_published(entry),
                points=None,
                self_text=self_text,
            )
            items.append((short_id, post))
        return items

    def _fetch_scores(self, client: httpx.Client, short_ids: list[str]) -> dict[str, int]:
        """Map ``short_id`` -> score from hottest.json, with a per-story fallback.

        The per-story fallback only fills BOUNDARY SKEW — a handful of RSS entries
        that dropped out of hottest between the two requests. If hottest.json
        wholly failed we skip it entirely rather than fan out ~25 serial
        per-story GETs; those posts just get no score (clout floors to 0.0).
        """
        scores: dict[str, int] = {}
        data = self._get_json(client, self.config.lobsters_hottest_url)
        if not isinstance(data, list):
            return scores  # hottest unavailable — don't hammer /s/{id} one-by-one
        for story in data:
            if isinstance(story, dict):
                sid, score = story.get("short_id"), story.get("score")
                if isinstance(sid, str) and isinstance(score, int):
                    scores[sid] = score
        base = _base_url(self.config.lobsters_rss_url)
        for short_id in short_ids:
            if short_id in scores:
                continue
            story = self._get_json(client, f"{base}/s/{short_id}.json")
            if isinstance(story, dict) and isinstance(story.get("score"), int):
                scores[short_id] = story["score"]
        return scores

    def _get_text(self, client: httpx.Client, url: str) -> str | None:
        for attempt in range(self.config.http_max_retries + 1):
            try:
                response = client.get(url)
                response.raise_for_status()
                return response.text
            except httpx.HTTPError:
                if attempt == self.config.http_max_retries:
                    logger.warning("lobste.rs GET failed: %s", url)
        return None

    def _get_json(self, client: httpx.Client, url: str) -> object:
        for attempt in range(self.config.http_max_retries + 1):
            try:
                response = client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                if attempt == self.config.http_max_retries:
                    logger.warning("lobste.rs GET failed: %s", url)
        return None


def _short_id(guid: object) -> str | None:
    """Extract the ``short_id`` from a ``.../s/{short_id}[/slug]`` guid/url."""
    if not isinstance(guid, str) or "/s/" not in guid:
        return None
    return guid.split("/s/", 1)[1].split("/", 1)[0] or None


def _is_self_post(link: str | None, short_id: str, tags: set[str]) -> bool:
    """True for Ask/Show/text posts — no external article to gist."""
    if tags & _SELF_POST_TAGS or not link:
        return True
    # A self-post's RSS link points back at its own lobste.rs thread.
    return f"/s/{short_id}" in link


def _published(entry: object) -> str | None:
    """ISO-8601 UTC from feedparser's ``published_parsed`` (already UTC), or None."""
    parsed = entry.get("published_parsed") if hasattr(entry, "get") else None
    if not parsed:
        return None
    try:
        return to_iso_z(datetime(*parsed[:6], tzinfo=UTC))
    except (TypeError, ValueError):
        return None


def _base_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
