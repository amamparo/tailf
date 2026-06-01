"""Tests for the sources layer: clout normalization + the two FeedSources
exercised over an ``httpx.MockTransport`` (no network)."""

from __future__ import annotations

import asyncio

import httpx
from hackergist.config import Config
from hackergist.sources import HackerNewsSource, LobstersSource, rank_clout

# --- clout (percentile rank) -------------------------------------------------


def test_rank_clout_spreads_to_unit_interval() -> None:
    assert rank_clout([10, 30, 50]) == [0.0, 0.5, 1.0]


def test_rank_clout_is_outlier_robust() -> None:
    # An outlier does NOT crush the rest toward 0 (min-max's failure mode): the
    # middle value stays mid-rank regardless of how far the top sits.
    assert rank_clout([10, 20, 1000]) == [0.0, 0.5, 1.0]


def test_rank_clout_all_equal_is_mid() -> None:
    assert rank_clout([7, 7, 7]) == [0.5, 0.5, 0.5]


def test_rank_clout_single_is_one() -> None:
    assert rank_clout([42]) == [1.0]


def test_rank_clout_missing_points_are_floor() -> None:
    # None -> 0.0; the present values rank among themselves.
    assert rank_clout([None, 10, 20]) == [0.0, 0.0, 1.0]


def test_rank_clout_all_missing() -> None:
    assert rank_clout([None, None]) == [0.0, 0.0]


# --- HackerNewsSource over a mock transport ----------------------------------

_T = 1_748_505_600  # fixed epoch (2025-05-29T08:00:00Z) for all fixture items


def _it(hn_id: int, **extra: object) -> dict:
    return {"id": hn_id, "type": "story", "title": f"Story {hn_id}", "time": _T, **extra}


_HN_ITEMS = {
    1: _it(1, url="https://a.com/1", by="a", score=100),
    2: _it(2, title="Ask HN: Two", by="b", score=20, text="<p>body &amp; more</p>"),  # no url
    3: _it(3, url="https://c.com/3", by="c", score=300),
    4: _it(4, url="https://c.com/3?utm_source=x", by="d", score=400),  # dupe url of 3
    5: _it(5, deleted=True),
}


def _hn_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("topstories.json"):
        return httpx.Response(200, json=[1, 2, 3])
    if path.endswith("beststories.json"):
        return httpx.Response(200, json=[3, 4, 5])
    if path.startswith("/v0/item/"):
        hn_id = int(path.rsplit("/", 1)[-1].removesuffix(".json"))
        return httpx.Response(200, json=_HN_ITEMS.get(hn_id))
    return httpx.Response(404)


def _run_hn() -> list:
    source = HackerNewsSource(Config())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_hn_handler)) as client:
            return await source._get_posts_async(client=client)

    return asyncio.run(go())


def test_hn_source_maps_dedupes_and_clouts() -> None:
    posts = _run_hn()
    by_url = {p.link: p for p in posts}

    # Item 5 (deleted) dropped; items 3 & 4 share a canonical url -> one post,
    # keeping the max score (400).
    assert {p.comments_url for p in posts}  # all have comments urls
    dupe = by_url["https://c.com/3"]
    assert dupe.points == 400  # max of 300/400 wins

    ask = next(p for p in posts if p.link is None)
    assert ask.source == "hn"
    assert ask.self_text is not None and "body & more" in ask.self_text
    assert ask.comments_url == "https://news.ycombinator.com/item?id=2"

    # clout is min-max over the batch's points (20, 100, 400) -> the 400 is 1.0.
    assert max(p.clout for p in posts) == 1.0
    assert min(p.clout for p in posts) == 0.0


# --- LobstersSource over a mock transport ------------------------------------

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Lobsters</title>
  <item>
    <title>QBE Compiler Backend</title>
    <link>https://c9x.me/compile/qbe.html</link>
    <guid>https://lobste.rs/s/aaa</guid>
    <comments>https://lobste.rs/s/aaa/qbe</comments>
    <pubDate>Mon, 01 Jun 2026 06:57:04 -0500</pubDate>
    <category>compilers</category>
  </item>
  <item>
    <title>What are you doing this week?</title>
    <link>https://lobste.rs/s/bbb/what_are_you_doing</link>
    <guid>https://lobste.rs/s/bbb</guid>
    <comments>https://lobste.rs/s/bbb/what_are_you_doing</comments>
    <pubDate>Mon, 01 Jun 2026 04:57:04 -0500</pubDate>
    <description>&lt;p&gt;Share what you're building.&lt;/p&gt;</description>
    <category>ask</category>
  </item>
  <item>
    <title>Zstandard in Rust</title>
    <link>https://trifectatech.org/zstd</link>
    <guid>https://lobste.rs/s/ccc</guid>
    <comments>https://lobste.rs/s/ccc/zstd</comments>
    <pubDate>Mon, 01 Jun 2026 08:11:00 -0500</pubDate>
    <category>rust</category>
  </item>
</channel></rss>
"""

_HOTTEST = [
    {"short_id": "aaa", "score": 50},
    {"short_id": "bbb", "score": 10},
    # ccc intentionally absent from hottest -> per-story fallback exercised.
]


def _lob_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/rss.rss":
        return httpx.Response(200, text=_RSS)
    if path == "/hottest.json":
        return httpx.Response(200, json=_HOTTEST)
    if path == "/s/ccc.json":
        return httpx.Response(200, json={"short_id": "ccc", "score": 30})
    return httpx.Response(404)


def _run_lobsters() -> list:
    source = LobstersSource(Config())
    with httpx.Client(transport=httpx.MockTransport(_lob_handler)) as client:
        return source._collect(client)


def test_lobsters_source_maps_self_posts_scores_and_clout() -> None:
    posts = _run_lobsters()
    assert len(posts) == 3
    by_comments = {p.comments_url: p for p in posts}

    qbe = by_comments["https://lobste.rs/s/aaa/qbe"]
    assert qbe.link == "https://c9x.me/compile/qbe.html"
    assert qbe.source == "lobsters"
    assert qbe.points == 50
    assert qbe.published == "2026-06-01T11:57:04Z"  # -0500 -> UTC

    ask = by_comments["https://lobste.rs/s/bbb/what_are_you_doing"]
    assert ask.link is None  # self-post (link points back at the thread)
    assert ask.self_text is not None and "Share what you're building" in ask.self_text
    assert ask.points == 10

    # ccc was missing from hottest.json -> recovered via /s/ccc.json fallback.
    zstd = by_comments["https://lobste.rs/s/ccc/zstd"]
    assert zstd.points == 30

    # clout min-max over (50, 10, 30): 50 -> 1.0, 10 -> 0.0.
    assert qbe.clout == 1.0
    assert ask.clout == 0.0
    assert zstd.clout == 0.5
