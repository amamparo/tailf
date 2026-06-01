"""Tests for the render fallback's network-free paths.

The actual headless rendering needs a real browser and is exercised by a live
`just index`, not here. These cover the cheap guards.
"""

from __future__ import annotations

import asyncio

from tailf.config import Config
from tailf.render import render_pages


def test_render_pages_empty_returns_empty_without_browser() -> None:
    # No stories -> no browser launch, just {}.
    assert asyncio.run(render_pages([], Config())) == {}
