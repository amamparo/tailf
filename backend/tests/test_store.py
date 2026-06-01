"""Tests for the store: merge reuses gists, prunes drops, keeps the union.

Uses a ``FakeFileSystem`` so nothing touches disk or S3.
"""

from __future__ import annotations

import json

from hackergist.config import Config
from hackergist.filesystem import FileSystem
from hackergist.models import DataFile, Gist, Story
from hackergist.store import Store, merge


class FakeFileSystem(FileSystem):
    """In-memory FileSystem for tests."""

    def __init__(self) -> None:
        self.blobs: dict[str, str] = {}

    def read_text(self, key: str) -> str:
        return self.blobs[key]

    def write_text(self, key: str, content: str) -> None:
        self.blobs[key] = content

    def exists(self, key: str) -> bool:
        return key in self.blobs


def _gist(text: str = "g", kind: str = "article") -> Gist:
    return Gist(text=text, model="claude-haiku-4-5", generated_at="2026-05-29T08:05:00Z", kind=kind)


def _story(hn_id: int, *, feeds, gist=None, points=100, url=None) -> Story:
    return Story(
        hn_id=hn_id,
        title=f"Story {hn_id}",
        url=url or f"https://example.com/{hn_id}",
        domain="example.com",
        comments_url=f"https://news.ycombinator.com/item?id={hn_id}",
        points=points,
        author="someone",
        published="2026-05-29T08:00:00Z",
        feeds=feeds,
        gist=gist,
    )


def test_merge_reuses_existing_gist_by_hn_id() -> None:
    reused = _gist("existing")
    current = DataFile(stories=[_story(1, feeds=["frontpage"], gist=reused)])
    # Same story re-fetched, now gist-less, with updated points.
    fresh = [_story(1, feeds=["best", "frontpage"], points=200)]

    result = merge(current, fresh, new_gists={})

    assert len(result.stories) == 1
    out = result.stories[0]
    assert out.gist is reused  # reused, not re-summarized
    assert out.points == 200  # refreshed from fresh fetch
    assert out.feeds == ["best", "frontpage"]  # feeds unioned/refreshed


def test_merge_refreshes_points_for_all_previously_scraped_stories() -> None:
    # Hotness is recomputed from points each render, so every run must refresh
    # points for ALL retained stories — including ones scraped (and gisted) in
    # past runs — not just newly-added ones.
    current = DataFile(
        stories=[
            _story(1, feeds=["frontpage"], gist=_gist(), points=10),
            _story(2, feeds=["best"], gist=_gist(), points=20),
        ]
    )
    # Same two stories re-fetched with higher scores; no new gisting happens.
    fresh = [
        _story(1, feeds=["frontpage"], points=111),
        _story(2, feeds=["best"], points=222),
    ]

    result = merge(current, fresh, new_gists={})

    by_id = {s.hn_id: s for s in result.stories}
    assert by_id[1].points == 111 and by_id[1].gist is not None  # refreshed, gist kept
    assert by_id[2].points == 222 and by_id[2].gist is not None


def test_merge_reuses_and_applies_images_by_hn_id() -> None:
    # Story 1 already has a stored image -> reused without re-fetch. Story 2 is
    # newly gisted this run and brings a fresh image.
    s1 = _story(1, feeds=["frontpage"], gist=_gist())
    s1.image = "https://example.com/1.png"
    current = DataFile(stories=[s1])
    fresh = [_story(1, feeds=["frontpage"]), _story(2, feeds=["best"])]

    result = merge(
        current,
        fresh,
        new_gists={2: _gist("new")},
        new_images={2: "https://example.com/2.png"},
    )

    by_id = {s.hn_id: s for s in result.stories}
    assert by_id[1].image == "https://example.com/1.png"  # reused
    assert by_id[2].image == "https://example.com/2.png"  # freshly applied


def test_merge_keeps_existing_gist_when_regist_fails() -> None:
    # A previously-gisted story is re-fetched gist-less and produces no new gist
    # this run (the source was unreachable). The existing gist MUST survive —
    # a null/failed re-gist never overwrites a good one.
    keep = _gist("keep me")
    current = DataFile(stories=[_story(1, feeds=["frontpage"], gist=keep)])
    fresh = [_story(1, feeds=["frontpage"])]  # gist=None on the fresh fetch

    result = merge(current, fresh, new_gists={})  # nothing newly gisted

    assert result.stories[0].gist is keep


def test_merge_never_overwrites_existing_gist_even_with_a_new_one() -> None:
    # Defensive: even if a new gist were somehow produced for an already-gisted
    # story, the existing one wins (a successful gist is immutable).
    keep = _gist("original")
    current = DataFile(stories=[_story(1, feeds=["frontpage"], gist=keep)])
    fresh = [_story(1, feeds=["frontpage"])]

    result = merge(current, fresh, new_gists={1: _gist("should not win")})

    assert result.stories[0].gist is keep
    assert result.stories[0].gist.text == "original"


def test_merge_applies_new_gist_for_new_story() -> None:
    current = DataFile(stories=[])
    fresh = [_story(2, feeds=["best"])]
    new = {2: _gist("brand new")}

    result = merge(current, fresh, new_gists=new)

    assert result.stories[0].gist.text == "brand new"


def test_merge_prunes_stories_absent_from_both_feeds() -> None:
    current = DataFile(
        stories=[
            _story(1, feeds=["frontpage"], gist=_gist()),
            _story(99, feeds=["best"], gist=_gist()),  # will drop out
        ]
    )
    fresh = [_story(1, feeds=["frontpage"])]  # only story 1 survives

    result = merge(current, fresh, new_gists={})

    ids = {s.hn_id for s in result.stories}
    assert ids == {1}


def test_merge_keeps_whole_union_no_cap() -> None:
    current = DataFile(stories=[])
    fresh = [_story(i, feeds=["frontpage"]) for i in range(1, 61)]
    result = merge(current, fresh, new_gists={})
    assert len(result.stories) == 60


def test_merge_stamps_generated_at() -> None:
    result = merge(DataFile.empty(), [_story(1, feeds=["best"])], {})
    assert result.generated_at is not None and result.generated_at.endswith("Z")


def test_merge_backfills_null_published_with_generated_at() -> None:
    # A fresh story with no parseable submit time -> published must not stay None
    # in the written contract; it is backfilled with the run's generated_at.
    story = _story(1, feeds=["best"])
    story.published = None
    result = merge(DataFile.empty(), [story], {})
    out = result.stories[0]
    assert out.published is not None
    assert out.published == result.generated_at


def test_store_load_empty_when_absent() -> None:
    store = Store(Config(), FakeFileSystem())
    loaded = store.load()
    assert loaded.stories == []


def test_store_load_handles_corrupt_file() -> None:
    fs = FakeFileSystem()
    fs.write_text("data.json", "{not valid json")
    store = Store(Config(), fs)
    assert store.load().stories == []


def test_store_write_then_load_round_trips() -> None:
    fs = FakeFileSystem()
    store = Store(Config(), fs)
    df = DataFile(
        stories=[_story(1, feeds=["best"], gist=_gist())],
        generated_at="2026-05-29T12:00:00Z",
    )

    store.write(df)
    # Persisted as valid JSON under the configured key.
    assert "data.json" in fs.blobs
    json.loads(fs.blobs["data.json"])

    loaded = store.load()
    assert loaded.stories[0].hn_id == 1
    assert loaded.stories[0].gist.text == "g"


def test_store_uses_configured_data_key() -> None:
    fs = FakeFileSystem()
    config = Config(data_key="custom.json")
    store = Store(config, fs)
    store.write(DataFile.empty())
    assert "custom.json" in fs.blobs
