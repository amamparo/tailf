"""Tests for the store: merge reuses gists/images by id, prunes drops, keeps the
union. Uses a ``FakeFileSystem`` so nothing touches disk or S3.
"""

from __future__ import annotations

import json

from tailf.config import Config
from tailf.filesystem import FileSystem
from tailf.models import DataFile
from tailf.store import Store, merge

from tests.conftest import make_discussion, make_gist, make_story


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


def test_merge_reuses_existing_gist_by_id() -> None:
    reused = make_gist("existing")
    current = DataFile(stories=[make_story(id="a", clout=0.5, gist=reused)])
    # Same story re-fetched, now gist-less, with refreshed clout/discussions.
    fresh = [make_story(id="a", clout=0.9)]

    result = merge(current, fresh, new_gists={})

    assert len(result.stories) == 1
    out = result.stories[0]
    assert out.gist is reused  # reused, not re-summarized
    assert out.clout == 0.9  # refreshed from fresh fetch


def test_merge_refreshes_clout_for_all_previously_scraped_stories() -> None:
    # The eventual sort consumes clout, so every run refreshes clout/discussions
    # for ALL retained stories — including ones gisted in past runs.
    current = DataFile(
        stories=[
            make_story(id="a", clout=0.1, gist=make_gist()),
            make_story(id="b", clout=0.2, gist=make_gist()),
        ]
    )
    fresh = [make_story(id="a", clout=0.7), make_story(id="b", clout=0.8)]

    result = merge(current, fresh, new_gists={})

    by_id = {s.id: s for s in result.stories}
    assert by_id["a"].clout == 0.7 and by_id["a"].gist is not None
    assert by_id["b"].clout == 0.8 and by_id["b"].gist is not None


def test_merge_reuses_and_applies_images_by_id() -> None:
    # Story "a" already has a stored image -> reused. Story "b" is newly gisted
    # this run and brings a fresh image.
    a = make_story(id="a", gist=make_gist(), image="https://example.com/1.png")
    current = DataFile(stories=[a])
    fresh = [make_story(id="a"), make_story(id="b")]

    result = merge(
        current,
        fresh,
        new_gists={"b": make_gist("new")},
        new_images={"b": "https://example.com/2.png"},
    )

    by_id = {s.id: s for s in result.stories}
    assert by_id["a"].image == "https://example.com/1.png"  # reused
    assert by_id["b"].image == "https://example.com/2.png"  # freshly applied


def test_merge_keeps_existing_gist_when_regist_fails() -> None:
    # A previously-gisted story re-fetched gist-less, no new gist this run -> the
    # existing gist MUST survive (a null/failed re-gist never overwrites a good one).
    keep = make_gist("keep me")
    current = DataFile(stories=[make_story(id="a", gist=keep)])
    fresh = [make_story(id="a")]

    result = merge(current, fresh, new_gists={})

    assert result.stories[0].gist is keep


def test_merge_never_overwrites_existing_gist_even_with_a_new_one() -> None:
    keep = make_gist("original")
    current = DataFile(stories=[make_story(id="a", gist=keep)])
    fresh = [make_story(id="a")]

    result = merge(current, fresh, new_gists={"a": make_gist("should not win")})

    assert result.stories[0].gist is keep
    assert result.stories[0].gist.text == "original"


def test_merge_reuses_one_gist_for_a_cross_posted_article() -> None:
    # A cross-posted article (id = canonical url) has ONE gist regardless of how
    # many discussions it carries; it's never re-gisted while present.
    keep = make_gist("shared")
    current = DataFile(stories=[make_story(id="https://ex.com/p", gist=keep)])
    fresh = [
        make_story(
            id="https://ex.com/p",
            discussions=[make_discussion("hn", clout=0.9), make_discussion("lobsters", clout=0.6)],
        )
    ]

    result = merge(current, fresh, new_gists={})

    assert result.stories[0].gist is keep
    assert len(result.stories[0].discussions) == 2


def test_merge_applies_new_gist_for_new_story() -> None:
    result = merge(DataFile(stories=[]), [make_story(id="b")], {"b": make_gist("brand new")})
    assert result.stories[0].gist.text == "brand new"


def test_merge_prunes_stories_absent_from_the_fresh_union() -> None:
    current = DataFile(
        stories=[make_story(id="a", gist=make_gist()), make_story(id="z", gist=make_gist())]
    )
    fresh = [make_story(id="a")]  # only "a" survives

    result = merge(current, fresh, new_gists={})

    assert {s.id for s in result.stories} == {"a"}


def test_merge_keeps_whole_union_no_cap() -> None:
    fresh = [make_story(id=f"https://example.com/{i}") for i in range(60)]
    result = merge(DataFile(stories=[]), fresh, new_gists={})
    assert len(result.stories) == 60


def test_merge_stamps_generated_at() -> None:
    result = merge(DataFile.empty(), [make_story(id="a")], {})
    assert result.generated_at is not None and result.generated_at.endswith("Z")


def test_merge_writes_schema_version_2() -> None:
    result = merge(DataFile.empty(), [make_story(id="a")], {})
    assert result.schema_version == 2


def test_merge_backfills_null_published_with_generated_at() -> None:
    story = make_story(id="a", published=None)
    result = merge(DataFile.empty(), [story], {})
    out = result.stories[0]
    assert out.published is not None
    assert out.published == result.generated_at


def test_store_load_empty_when_absent() -> None:
    store = Store(Config(), FakeFileSystem())
    assert store.load().stories == []


def test_store_load_handles_corrupt_file() -> None:
    fs = FakeFileSystem()
    fs.write_text("data.json", "{not valid json")
    store = Store(Config(), fs)
    assert store.load().stories == []


def test_store_write_then_load_round_trips() -> None:
    fs = FakeFileSystem()
    store = Store(Config(), fs)
    df = DataFile(
        stories=[make_story(id="a", gist=make_gist())], generated_at="2026-05-29T12:00:00Z"
    )

    store.write(df)
    assert "data.json" in fs.blobs
    json.loads(fs.blobs["data.json"])

    loaded = store.load()
    assert loaded.stories[0].id == "a"
    assert loaded.stories[0].gist.text == "g"


def test_store_uses_configured_data_key() -> None:
    fs = FakeFileSystem()
    store = Store(Config(data_key="custom.json"), fs)
    store.write(DataFile.empty())
    assert "custom.json" in fs.blobs
