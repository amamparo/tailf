"""The end-to-end run: fetch -> diff -> extract+gist new -> merge -> write.

``run(injector)`` is the single entry point used by both the Lambda handler
and the local CLI. It returns a small summary dict for logging::

    {"fetched": N, "new": N, "gisted": N, "rendered": N, "no_gist": N,
     "pruned": N, "total": N}

Gisting new stories happens in two phases:

1. **Static** — fetch + trafilatura extraction + summarize, run concurrently.
2. **Render fallback** — stories that produced no gist (typically JS/SPA pages)
   are re-fetched with headless Chromium (``render.py``), re-extracted, and
   re-summarized. This recovers client-side-rendered content static scraping
   can't see. It degrades gracefully if Chromium is unavailable.
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
from .fetch import fetch_feeds, union_feeds
from .models import DataFile, Gist, Story
from .render import render_pages
from .store import Store, merge
from .summarize import summarize

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import Anthropic

logger = logging.getLogger(__name__)


def run(injector: Injector) -> dict[str, int]:
    """Run one full pipeline pass and return a summary dict."""
    config = injector.get(Config)
    store = injector.get(Store)

    # 1. Fetch + union the two feeds.
    feeds = fetch_feeds(config)
    fresh = union_feeds(feeds)

    # 2. Load current state and figure out which stories are NEW (no gist yet).
    current = store.load()
    gisted_ids = {s.hn_id for s in current.stories if s.gist is not None}
    new_stories = [s for s in fresh if s.hn_id not in gisted_ids]

    # 3. Gist all new stories (no per-run cap — gist reuse by hn_id already keeps
    #    steady-state cost low). Static pass + headless-render fallback, all
    #    asyncio-driven with tqdm progress.
    new_gists, new_images, rendered = asyncio.run(
        _gist_new_stories(new_stories, config, injector)
    )

    # 4. Merge (reuse gists/images, prune dropped stories) and persist.
    next_file = merge(current, fresh, new_gists, new_images)
    store.write(next_file)

    summary = {
        "fetched": len(fresh),
        "new": len(new_stories),
        "gisted": len(new_gists),
        "rendered": rendered,  # of the gists, how many came from the render fallback
        "no_gist": len(new_stories) - len(new_gists),  # unreadable (paywall/dead)
        "pruned": _count_pruned(current, fresh),
        "total": len(next_file.stories),
    }
    logger.info("pipeline run complete: %s", summary)
    return summary


# A gisted result: the gist plus any preview image captured in the same pass.
Gisted = tuple[Gist, str | None]


async def _gist_new_stories(
    stories: list[Story],
    config: Config,
    injector: Injector,
) -> tuple[dict[int, Gist], dict[int, str], int]:
    """Gist ``stories`` via the static pass + render fallback.

    Returns ``(new_gists, new_images, n_rendered)``: the gists by hn_id, the
    preview images by hn_id (only where present), and how many gists came from
    the headless-render fallback (for the run summary).
    """
    if not stories:
        return {}, {}, 0

    from anthropic import Anthropic

    client: Anthropic = injector.get(Anthropic)

    # Phase 1: static fetch + extract + summarize.
    gisted: dict[int, Gisted] = await _gist_static(stories, client, config)

    # Phase 2: render fallback for stories with no gist and an http(s) URL —
    # these are usually SPA/JS pages whose server HTML had no real content.
    n_rendered = 0
    if config.render_enabled:
        unresolved = [
            s
            for s in stories
            if s.hn_id not in gisted
            and s.url
            and s.url.startswith(("http://", "https://"))
        ]
        if unresolved:
            recovered = await _gist_rendered(unresolved, client, config)
            gisted.update(recovered)
            n_rendered = len(recovered)

    new_gists = {hn_id: gist for hn_id, (gist, _img) in gisted.items()}
    new_images = {hn_id: img for hn_id, (_gist, img) in gisted.items() if img}
    return new_gists, new_images, n_rendered


async def _gist_static(
    stories: list[Story], client: Anthropic, config: Config
) -> dict[int, Gisted]:
    """Concurrent static extract + summarize. Each (blocking) story runs in a
    thread under a semaphore; a tqdm bar reports progress (off when not a TTY)."""
    headers = {"User-Agent": config.user_agent}
    timeout = httpx.Timeout(config.http_timeout_seconds)

    def work(story: Story) -> tuple[int, Gist | None, str | None]:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as http:
            extracted = extract(story, config, client=http)
        gist = summarize(client, story.title, extracted, config)
        image = extracted.image if extracted else None
        return story.hn_id, gist, image

    semaphore = asyncio.Semaphore(max(1, min(config.extract_concurrency, len(stories))))

    async def run_one(story: Story) -> tuple[int, Gist | None, str | None]:
        async with semaphore:
            return await asyncio.to_thread(work, story)

    results = await tqdm_asyncio.gather(
        *(run_one(s) for s in stories),
        desc="gisting",
        unit="story",
        disable=not sys.stderr.isatty(),
    )
    return {hn_id: (gist, image) for hn_id, gist, image in results if gist is not None}


async def _gist_rendered(
    stories: list[Story], client: Anthropic, config: Config
) -> dict[int, Gisted]:
    """Render unresolved pages with headless Chromium, then extract + summarize
    the rendered HTML. Returns gists only for pages that yielded real content."""
    rendered_html = await render_pages(stories, config)  # {hn_id: html}
    if not rendered_html:
        return {}
    by_id = {s.hn_id: s for s in stories}

    def gist_from_html(hn_id: int, html: str) -> tuple[int, Gist | None, str | None]:
        story = by_id[hn_id]
        extracted = extract_article_from_html(html, story.url or "", config)
        if extracted is None:
            return hn_id, None, None
        gist = summarize(client, story.title, extracted, config)
        return hn_id, gist, extracted.image

    semaphore = asyncio.Semaphore(max(1, min(config.extract_concurrency, len(rendered_html))))

    async def run_one(item: tuple[int, str]) -> tuple[int, Gist | None, str | None]:
        hn_id, html = item
        async with semaphore:
            return await asyncio.to_thread(gist_from_html, hn_id, html)

    results = await asyncio.gather(*(run_one(it) for it in rendered_html.items()))
    return {hn_id: (gist, image) for hn_id, gist, image in results if gist is not None}


def _count_pruned(current: DataFile, fresh: list[Story]) -> int:
    """Count stories present before but absent from both feeds now."""
    fresh_ids = {s.hn_id for s in fresh}
    return sum(1 for s in current.stories if s.hn_id not in fresh_ids)
