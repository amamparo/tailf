"""The end-to-end run: fetch -> merge -> extract+gist new -> merge -> write.

``run(injector)`` is the single entry point used by both the Lambda handler
and the local CLI. It returns a small summary dict for logging.

Each enabled :class:`~tailf.sources.FeedSource` yields ``Post``s; these are
merged ACROSS sources by canonical URL into ``Story`` records (one article =
one record with one-or-more ``discussions``). The merge happens BEFORE gisting,
so a link posted to both HN and lobste.rs is fetched + summarized exactly once.

Gisting new stories happens in two phases:

1. **Static** — fetch + trafilatura extraction + summarize, run concurrently.
2. **Render fallback** — stories that produced no gist (typically JS/SPA pages)
   are re-fetched with headless Chromium (``render.py``), re-extracted, and
   re-summarized. It degrades gracefully if Chromium is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

import httpx
from injector import Injector
from tqdm.asyncio import tqdm as tqdm_asyncio

from .config import Config
from .extract import extract, extract_article_from_html
from .models import DataFile, Discussion, Gist, Story, discussion_key, domain_of, resolve_title
from .render import render_pages
from .sources import Post, SourceRegistry
from .store import Store, merge
from .summarize import summarize

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import Anthropic

logger = logging.getLogger(__name__)


def run(injector: Injector) -> dict[str, int]:
    """Run one full pipeline pass and return a summary dict."""
    config = injector.get(Config)
    store = injector.get(Store)
    registry = injector.get(SourceRegistry)

    # 1. Fetch each enabled source, then merge across sources into Story records.
    posts_by_source = {source.name: source.get_posts() for source in registry.sources}
    fresh = merge_posts(posts_by_source)

    # 2. Load current state and figure out which stories still need a gist.
    current = store.load()
    new_stories = _stories_needing_gist(fresh, current)

    # 3. Gist all new stories (gist reuse by story.id keeps steady-state cost
    #    low; a cross-posted article is one story → gisted once). Static pass +
    #    headless-render fallback, asyncio-driven with tqdm progress.
    new_gists, new_images, new_seo_titles, rendered = asyncio.run(
        _gist_new_stories(new_stories, config, injector)
    )

    # 4. Merge (reuse gists/images/titles, prune dropped stories) and persist.
    next_file = merge(current, fresh, new_gists, new_images, new_seo_titles)
    store.write(next_file)

    summary = {
        "fetched": len(fresh),
        # Per-source post counts so an empty/short source is VISIBLE in
        # CloudWatch (a 0 here is the smoking gun for an upstream failure).
        **{name: len(posts) for name, posts in posts_by_source.items()},
        "new": len(new_stories),
        "gisted": len(new_gists),
        "rendered": rendered,  # of the gists, how many came from the render fallback
        "no_gist": len(new_stories) - len(new_gists),  # unreadable (paywall/dead)
        "pruned": _count_pruned(current, fresh),
        "total": len(next_file.stories),
    }
    logger.info("pipeline run complete: %s", summary)
    return summary


def merge_posts(posts_by_source: dict[str, list[Post]]) -> list[Story]:
    """Merge per-source ``Post``s into ``Story`` records, deduped by canonical URL.

    Link posts that share a canonical URL (across or within sources) collapse
    into ONE ``Story`` with a ``Discussion`` per source. Self/text posts key on
    a ``self:`` comments URL and never merge across sources. First-appearance
    order is preserved for determinism.
    """
    groups: dict[str, list[Post]] = {}
    order: list[str] = []
    for posts in posts_by_source.values():
        for post in posts:
            key = discussion_key(post.link, post.comments_url)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(post)
    return [_story_from_posts(key, groups[key]) for key in order]


def _story_from_posts(key: str, posts: list[Post]) -> Story:
    """Build one ``Story`` from the posts sharing a record ``key``."""
    # Deterministic order: oldest submit first (dated before undated), then source.
    ordered = sorted(posts, key=lambda p: (p.published is None, p.published or "", p.source))
    url = next((p.link for p in ordered if p.link), None)
    discussions = [
        Discussion(
            source=p.source,
            comments_url=p.comments_url,
            clout=p.clout,
            points=p.points,
            published=p.published,
            title=p.title,
        )
        for p in ordered
    ]
    return Story(
        id=key,
        # Provisional title — the article's own page <title> isn't known until
        # the gist fetch, where it's captured and applied in store.merge. This
        # handles the agree / higher-clout cases (see resolve_title).
        title=resolve_title(discussions, None),
        url=url,
        domain=domain_of(url),
        # Oldest submit time — closest to the article's publish date (display).
        published=min((p.published for p in posts if p.published), default=None),
        clout=max((p.clout for p in posts), default=0.0),
        discussions=discussions,
        self_text=next((p.self_text for p in ordered if p.self_text), None),
    )


# A gisted result: the gist, any preview image, and the page's own <title>,
# all captured in the same fetch/extract pass.
Gisted = tuple[Gist, str | None, str | None]


async def _gist_new_stories(
    stories: list[Story],
    config: Config,
    injector: Injector,
) -> tuple[dict[str, Gist], dict[str, str], dict[str, str], int]:
    """Gist ``stories`` via the static pass + render fallback.

    Returns ``(new_gists, new_images, new_seo_titles, n_rendered)``: by
    ``story.id``, the gists, the preview images (where present), the page
    titles (where captured), and how many gists came from the headless-render
    fallback (for the run summary).
    """
    if not stories:
        return {}, {}, {}, 0

    from anthropic import Anthropic

    client: Anthropic = injector.get(Anthropic)

    # Phase 1: static fetch + extract + summarize.
    gisted: dict[str, Gisted] = await _gist_static(stories, client, config)

    # Phase 2: render fallback for stories with no gist and an http(s) URL —
    # these are usually SPA/JS pages whose server HTML had no real content.
    n_rendered = 0
    if config.render_enabled:
        unresolved = [
            s
            for s in stories
            if s.id not in gisted and s.url and s.url.startswith(("http://", "https://"))
        ]
        if unresolved:
            recovered = await _gist_rendered(unresolved, client, config)
            gisted.update(recovered)
            n_rendered = len(recovered)

    new_gists = {key: g for key, (g, _img, _t) in gisted.items()}
    new_images = {key: img for key, (_g, img, _t) in gisted.items() if img}
    new_seo_titles = {key: t for key, (_g, _img, t) in gisted.items() if t}
    return new_gists, new_images, new_seo_titles, n_rendered


async def _gist_static(
    stories: list[Story], client: Anthropic, config: Config
) -> dict[str, Gisted]:
    """Concurrent static extract + summarize. Each (blocking) story runs in a
    thread under a semaphore; a tqdm bar reports progress (off when not a TTY)."""
    headers = {"User-Agent": config.user_agent}
    timeout = httpx.Timeout(config.http_timeout_seconds)

    def work(story: Story) -> tuple[str, Gist | None, str | None, str | None]:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as http:
            extracted = extract(story, config, client=http)
        gist = summarize(client, story.title, extracted, config)
        image = extracted.image if extracted else None
        seo_title = extracted.seo_title if extracted else None
        return story.id, gist, image, seo_title

    semaphore = asyncio.Semaphore(max(1, min(config.extract_concurrency, len(stories))))

    async def run_one(story: Story) -> tuple[str, Gist | None, str | None, str | None]:
        async with semaphore:
            return await asyncio.to_thread(work, story)

    results = await tqdm_asyncio.gather(
        *(run_one(s) for s in stories),
        desc="gisting",
        unit="story",
        disable=not sys.stderr.isatty(),
    )
    return {
        key: (gist, image, seo_title)
        for key, gist, image, seo_title in results
        if gist is not None
    }


async def _gist_rendered(
    stories: list[Story], client: Anthropic, config: Config
) -> dict[str, Gisted]:
    """Render unresolved pages with headless Chromium, then extract + summarize
    the rendered HTML. Returns gists only for pages that yielded real content."""
    rendered_html = await render_pages(stories, config)  # {story.id: html}
    if not rendered_html:
        return {}
    by_id = {s.id: s for s in stories}

    def gist_from_html(key: str, html: str) -> tuple[str, Gist | None, str | None, str | None]:
        story = by_id[key]
        extracted = extract_article_from_html(html, story.url or "", config)
        if extracted is None:
            return key, None, None, None
        gist = summarize(client, story.title, extracted, config)
        return key, gist, extracted.image, extracted.seo_title

    semaphore = asyncio.Semaphore(max(1, min(config.extract_concurrency, len(rendered_html))))

    async def run_one(item: tuple[str, str]) -> tuple[str, Gist | None, str | None, str | None]:
        key, html = item
        async with semaphore:
            return await asyncio.to_thread(gist_from_html, key, html)

    results = await asyncio.gather(*(run_one(it) for it in rendered_html.items()))
    return {
        key: (gist, image, seo_title)
        for key, gist, image, seo_title in results
        if gist is not None
    }


def _stories_needing_gist(fresh: list[Story], current: DataFile) -> list[Story]:
    """The stories to gist this run: those without an existing non-null gist.

    A story that ALREADY has a gist is never returned here, so it is never
    re-summarized (cost control + stability) — a successful gist is sticky for as
    long as the story stays in the feed. Stories that previously failed to gist
    (gist is null) ARE returned, so they're retried each run until one lands.
    """
    gisted_ids = {s.id for s in current.stories if s.gist is not None}
    return [s for s in fresh if s.id not in gisted_ids]


def _count_pruned(current: DataFile, fresh: list[Story]) -> int:
    """Count stories present before but absent from the fresh union now."""
    fresh_ids = {s.id for s in fresh}
    return sum(1 for s in current.stories if s.id not in fresh_ids)
