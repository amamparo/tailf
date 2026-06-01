"""Tests for pipeline logic that doesn't need network/LLM/injector:
``merge_posts`` (cross-source merge) and the gist-selection/prune helpers.
"""

from __future__ import annotations

from hackergist.models import DataFile
from hackergist.pipeline import _count_pruned, _stories_needing_gist, merge_posts

from tests.conftest import make_gist, make_post, make_story

# --- merge_posts -------------------------------------------------------------


def test_merge_posts_merges_same_url_across_sources_into_one_card() -> None:
    hn = make_post("hn", link="https://ex.com/post", published="2026-05-29T08:00:00Z", clout=0.9)
    lob = make_post(
        "lobsters",
        link="https://ex.com/post?utm_source=x",  # same canonical url
        published="2026-05-30T08:00:00Z",
        clout=0.6,
    )
    stories = merge_posts({"hn": [hn], "lobsters": [lob]})

    assert len(stories) == 1
    story = stories[0]
    assert story.id == "https://ex.com/post"
    assert story.url == "https://ex.com/post"
    # Both discussions kept, ordered oldest-submit-first (hn before lobsters).
    assert [d.source for d in story.discussions] == ["hn", "lobsters"]
    assert story.clout == 0.9  # max across discussions
    assert story.published == "2026-05-29T08:00:00Z"  # oldest


def test_merge_posts_keeps_different_urls_separate() -> None:
    hn = make_post("hn", link="https://ex.com/a")
    lob = make_post("lobsters", link="https://ex.com/b")
    stories = merge_posts({"hn": [hn], "lobsters": [lob]})
    assert {s.id for s in stories} == {"https://ex.com/a", "https://ex.com/b"}


def test_merge_posts_self_posts_never_merge_across_sources() -> None:
    # Self-posts have no link -> namespaced keys; an HN Ask and a lobste.rs ask
    # are distinct cards even though neither has an article URL.
    hn = make_post("hn", link=None, comments_url="https://news.ycombinator.com/item?id=1")
    lob = make_post("lobsters", link=None, comments_url="https://lobste.rs/s/abc")
    stories = merge_posts({"hn": [hn], "lobsters": [lob]})
    assert len(stories) == 2
    assert all(s.url is None for s in stories)
    assert all(len(s.discussions) == 1 for s in stories)


def test_merge_posts_discussion_order_is_deterministic_by_oldest() -> None:
    # lobsters submitted earlier than HN -> lobsters is discussions[0] (primary).
    hn = make_post("hn", link="https://ex.com/p", published="2026-05-30T00:00:00Z")
    lob = make_post("lobsters", link="https://ex.com/p", published="2026-05-29T00:00:00Z")
    # Pass HN first to prove ordering is by time, not arrival.
    stories = merge_posts({"hn": [hn], "lobsters": [lob]})
    assert [d.source for d in stories[0].discussions] == ["lobsters", "hn"]


def test_merge_posts_carries_self_text_for_self_post() -> None:
    post = make_post(
        "hn", link=None, self_text="the body", comments_url="https://news.ycombinator.com/item?id=9"
    )
    stories = merge_posts({"hn": [post]})
    assert stories[0].self_text == "the body"
    assert stories[0].id == "self:https://news.ycombinator.com/item?id=9"


# --- gist selection / prune --------------------------------------------------


def test_already_gisted_story_is_never_registed() -> None:
    current = DataFile(
        stories=[make_story(id="a", gist=make_gist()), make_story(id="b", gist=None)]
    )
    fresh = [make_story(id="a"), make_story(id="b"), make_story(id="c")]

    needing = _stories_needing_gist(fresh, current)

    assert {s.id for s in needing} == {"b", "c"}  # "a" (already gisted) is not re-gisted


def test_empty_current_gists_everything() -> None:
    fresh = [make_story(id="a"), make_story(id="b")]
    needing = _stories_needing_gist(fresh, DataFile.empty())
    assert {s.id for s in needing} == {"a", "b"}


def test_count_pruned_counts_stories_absent_now() -> None:
    current = DataFile(stories=[make_story(id="a"), make_story(id="b"), make_story(id="c")])
    fresh = [make_story(id="a")]
    assert _count_pruned(current, fresh) == 2
