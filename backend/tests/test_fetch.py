"""Tests for fetch: map HN Firebase items to Stories; union + dedupe.

The id-list -> item-hydrate -> Story flow is exercised end-to-end with an
``httpx.MockTransport`` (no network); the pure mapping/union helpers are tested
directly.
"""

from __future__ import annotations

import asyncio

import httpx
from hackergist.config import Config
from hackergist.fetch import (
    _fetch_feeds_async,
    _item_to_story,
    union_feeds,
)
from hackergist.models import Story, iso_from_epoch

# --- item -> Story mapping ----------------------------------------------------


def test_item_to_story_maps_fields() -> None:
    item = {
        "id": 40000001,
        "type": "story",
        "title": "A deep dive into modern CPU caches",
        "url": "https://example.com/cpu-caches",
        "by": "alice",
        "score": 234,
        "time": 1_748_505_600,  # 2025-05-29T08:00:00Z
        "descendants": 42,
    }
    story = _item_to_story(item, ["frontpage"])
    assert story is not None
    assert story.hn_id == 40000001
    assert story.title == "A deep dive into modern CPU caches"
    assert story.url == "https://example.com/cpu-caches"
    assert story.domain == "example.com"
    assert story.comments_url == "https://news.ycombinator.com/item?id=40000001"
    assert story.points == 234
    assert story.author == "alice"
    assert story.published == "2025-05-29T08:00:00Z"
    assert story.feeds == ["frontpage"]
    assert story.gist is None


def test_ask_hn_post_has_null_url_and_captures_self_text() -> None:
    item = {
        "id": 40000002,
        "type": "story",
        "title": "Ask HN: how do you run forty services?",
        # no "url" -> Ask/text post
        "by": "bob",
        "score": 12,
        "time": 1_748_505_600,
        "text": "We run <i>forty services</i> and it&#x27;s a lot. <a>link</a>",
    }
    story = _item_to_story(item, ["frontpage"])
    assert story is not None
    assert story.url is None
    assert story.domain is None
    assert story.comments_url == "https://news.ycombinator.com/item?id=40000002"
    assert story.hn_text is not None
    assert "forty services" in story.hn_text
    # HTML tags stripped, entities unescaped.
    assert "<i>" not in story.hn_text
    assert "it's a lot" in story.hn_text


def test_item_to_story_drops_deleted_dead_and_wrong_type() -> None:
    base = {"id": 1, "type": "story", "title": "x", "time": 1_748_505_600}
    assert _item_to_story({**base, "deleted": True}, ["best"]) is None
    assert _item_to_story({**base, "dead": True}, ["best"]) is None
    assert _item_to_story({**base, "type": "comment"}, ["best"]) is None
    assert _item_to_story(None, ["best"]) is None
    assert _item_to_story({"type": "story"}, ["best"]) is None  # no int id


def test_item_to_story_tolerates_missing_optional_fields() -> None:
    # A job with no score/time/by still yields a story (points/published null).
    story = _item_to_story(
        {"id": 5, "type": "job", "title": "We're hiring", "url": "https://co.example/jobs"},
        ["frontpage"],
    )
    assert story is not None
    assert story.points is None
    assert story.published is None
    assert story.author is None
    assert story.domain == "co.example"


def test_iso_from_epoch() -> None:
    assert iso_from_epoch(0) == "1970-01-01T00:00:00Z"
    assert iso_from_epoch(1_748_505_600) == "2025-05-29T08:00:00Z"
    assert iso_from_epoch(None) is None
    assert iso_from_epoch("not-a-number") is None


# --- end-to-end fetch via MockTransport ---------------------------------------

_T = 1_748_505_600  # fixed epoch for all fixture items


def _it(hn_id: int, **extra: object) -> dict:
    return {"id": hn_id, "type": "story", "title": f"Story {hn_id}", "time": _T, **extra}


_ITEMS = {
    1: _it(1, url="https://a.com/1", by="a", score=100),
    2: _it(2, title="Ask HN: Two", by="b", score=20, text="<p>body</p>"),  # no url
    3: _it(3, url="https://c.com/3", by="c", score=300),
    4: _it(4, url="https://d.com/4", by="d", score=400),
    5: _it(5, deleted=True),
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("topstories.json"):
        return httpx.Response(200, json=[1, 2, 3])
    if path.endswith("beststories.json"):
        return httpx.Response(200, json=[3, 4, 5])  # 3 overlaps; 5 is deleted
    if path.startswith("/v0/item/"):
        hn_id = int(path.rsplit("/", 1)[-1].removesuffix(".json"))
        item = _ITEMS.get(hn_id)
        return httpx.Response(200, json=item if item is not None else None)
    return httpx.Response(404)


def _run_fetch() -> dict[str, list[Story]]:
    async def go() -> dict[str, list[Story]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await _fetch_feeds_async(Config(), client=client)

    return asyncio.run(go())


def test_fetch_feeds_hydrates_and_tags_membership() -> None:
    feeds = _run_fetch()
    fp_ids = [s.hn_id for s in feeds["frontpage"]]
    best_ids = [s.hn_id for s in feeds["best"]]

    assert fp_ids == [1, 2, 3]
    assert best_ids == [3, 4]  # 5 (deleted) dropped
    # The story in both lists is the same object, tagged with both feeds.
    three = next(s for s in feeds["frontpage"] if s.hn_id == 3)
    assert three.feeds == ["best", "frontpage"]


def test_fetch_then_union_dedupes_and_orders() -> None:
    feeds = _run_fetch()
    union = union_feeds(feeds)
    ids = [s.hn_id for s in union]

    # frontpage ids first (in order), then best-only ids; story 3 once.
    assert ids == [1, 2, 3, 4]
    by_id = {s.hn_id: s for s in union}
    assert by_id[3].feeds == ["best", "frontpage"]
    assert by_id[2].url is None and by_id[2].hn_text is not None


# --- union helper (source-agnostic) -------------------------------------------


def _story(hn_id: int, feed: str, *, url: str | None = None, points: int | None = None) -> Story:
    return Story(
        hn_id=hn_id,
        title=f"Story {hn_id}",
        url=url,
        domain=None,
        comments_url=f"https://news.ycombinator.com/item?id={hn_id}",
        points=points,
        author=None,
        published="2025-05-29T08:00:00Z",
        feeds=[feed],
    )


def test_union_dedupes_by_hn_id_and_unions_feeds() -> None:
    front = [_story(1, "frontpage", points=234), _story(2, "frontpage")]
    best = [_story(1, "best", points=999), _story(3, "best")]
    union = union_feeds({"frontpage": front, "best": best})
    by_id = {s.hn_id: s for s in union}

    assert sum(1 for s in union if s.hn_id == 1) == 1
    assert by_id[1].feeds == ["best", "frontpage"]
    assert {s.hn_id for s in union} == {1, 2, 3}
    # frontpage seen first -> its points (234) retained; later non-null only fills gaps.
    assert by_id[1].points == 234


def test_union_dedupes_by_canonical_url_across_ids() -> None:
    # Same article reposted under a different HN id + tracking param -> one entry.
    front = [_story(10, "frontpage", url="https://ex.com/post")]
    best = [_story(11, "best", url="https://ex.com/post?utm_source=x")]
    union = union_feeds({"frontpage": front, "best": best})
    assert len(union) == 1
    assert union[0].hn_id == 10


def test_union_preserves_first_appearance_order() -> None:
    front = [_story(1, "frontpage"), _story(2, "frontpage")]
    best = [_story(2, "best"), _story(3, "best")]
    union = union_feeds({"frontpage": front, "best": best})
    assert [s.hn_id for s in union] == [1, 2, 3]
