"""Tests for pipeline selection logic that doesn't need network/LLM/injector."""

from __future__ import annotations

from hackergist.models import DataFile, Gist, Story
from hackergist.pipeline import _stories_needing_gist


def _gist() -> Gist:
    return Gist(
        text="g", model="claude-haiku-4-5", generated_at="2026-05-29T08:05:00Z", kind="article"
    )


def _story(hn_id: int, *, gist: Gist | None = None) -> Story:
    return Story(
        hn_id=hn_id,
        title=f"Story {hn_id}",
        url=f"https://example.com/{hn_id}",
        domain="example.com",
        comments_url=f"https://news.ycombinator.com/item?id={hn_id}",
        points=100,
        author="someone",
        published="2026-05-29T08:00:00Z",
        feeds=["frontpage"],
        gist=gist,
    )


def test_already_gisted_story_is_never_registed() -> None:
    # Story 1 already has a gist -> excluded (never re-summarized).
    # Story 2 is present but gist-less -> retried.
    # Story 3 is brand new -> gisted.
    current = DataFile(stories=[_story(1, gist=_gist()), _story(2, gist=None)])
    fresh = [_story(1), _story(2), _story(3)]

    needing = _stories_needing_gist(fresh, current)

    ids = {s.hn_id for s in needing}
    assert ids == {2, 3}  # 1 (already gisted) is NOT re-gisted


def test_empty_current_gists_everything() -> None:
    fresh = [_story(1), _story(2)]
    needing = _stories_needing_gist(fresh, DataFile.empty())
    assert {s.hn_id for s in needing} == {1, 2}
