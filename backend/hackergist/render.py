"""Headless-Chromium fallback for client-side-rendered (SPA) pages.

Static extraction (``extract.py``) can't see content that JavaScript paints in
the browser — those pages return an "enable JavaScript" shell. When the static
path yields no usable text, ``pipeline.py`` hands the still-unresolved stories
here: we launch ONE headless Chromium (via Playwright), render each URL, wait a
beat for the SPA to hydrate, and return the rendered HTML. The pipeline then
runs that HTML back through :func:`extract.extract_article_from_html`.

Everything degrades gracefully: if Playwright or its Chromium isn't installed
(or a page errors / times out), we just return fewer results and those stories
stay gist-less — the run never fails because rendering is unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from tqdm.asyncio import tqdm as tqdm_asyncio

from .config import Config
from .models import Story

logger = logging.getLogger(__name__)

# Chromium flags for constrained/containerized envs (Lambda). NOTE: we do NOT
# use --single-process / --no-zygote — they crash Chromium as soon as it juggles
# more than one context/page concurrently. --disable-dev-shm-usage is the key
# Lambda flag (its /dev/shm is tiny); keep render_concurrency low to bound RAM.
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


async def render_pages(stories: list[Story], config: Config) -> dict[int, str]:
    """Render each story's URL with headless Chromium.

    Returns ``{hn_id: rendered_html}`` for the pages that rendered successfully.
    Stories without an http(s) url are skipped by the caller. Returns ``{}`` (no
    crash) if Playwright/Chromium is unavailable.
    """
    if not stories:
        return {}
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed; skipping JS-render fallback")
        return {}

    semaphore = asyncio.Semaphore(max(1, config.render_concurrency))
    out: dict[int, str] = {}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            try:

                async def one(story: Story) -> tuple[int, str | None]:
                    async with semaphore:
                        html = await _render_one(browser, story.url, config)
                    return story.hn_id, html

                results = await tqdm_asyncio.gather(
                    *(one(s) for s in stories),
                    desc="rendering JS",
                    unit="page",
                    disable=not sys.stderr.isatty(),
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - browser launch can fail many ways
        # e.g. Chromium not installed (`playwright install chromium`) or no libs
        # in the runtime. Degrade to "no rendered pages" rather than failing.
        logger.warning("headless rendering unavailable (%s); skipping fallback", exc)
        return {}

    for hn_id, html in results:
        if html:
            out[hn_id] = html
    return out


async def _render_one(browser: object, url: str | None, config: Config) -> str | None:
    """Render one URL and return its post-JS HTML, or ``None`` on any failure."""
    if not url:
        return None
    context = None
    try:
        context = await browser.new_context(  # type: ignore[attr-defined]
            user_agent=config.user_agent,
            java_script_enabled=True,
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=config.render_timeout_ms)
        # Let client-side frameworks paint content after the initial load.
        await page.wait_for_timeout(config.render_wait_ms)
        return await page.content()
    except Exception as exc:  # noqa: BLE001 - navigation/timeout/crash -> no html
        logger.info("render failed for %s: %s", url, exc)
        return None
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
