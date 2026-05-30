"""Tests for fetch: parse saved hnrss XML into Stories; union + dedupe.

No network: we read the saved fixtures and call the pure parse/union helpers.
"""

from __future__ import annotations

from pathlib import Path

from hackergist.fetch import parse_feed, union_feeds

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_frontpage_maps_fields() -> None:
    stories = parse_feed(_load("frontpage.xml"), "frontpage")
    by_id = {s.hn_id: s for s in stories}

    cpu = by_id[40000001]
    assert cpu.title == "A deep dive into modern CPU caches"
    assert cpu.url == "https://example.com/cpu-caches"
    assert cpu.domain == "example.com"
    assert cpu.comments_url == "https://news.ycombinator.com/item?id=40000001"
    assert cpu.points == 234
    assert cpu.author == "alice"
    assert cpu.published == "2026-05-29T08:00:00Z"
    assert cpu.feeds == ["frontpage"]
    assert cpu.gist is None


def test_ask_hn_post_has_null_url_and_captures_self_text() -> None:
    stories = parse_feed(_load("frontpage.xml"), "frontpage")
    ask = next(s for s in stories if s.hn_id == 40000002)

    assert ask.url is None  # no external link
    assert ask.domain is None
    assert ask.comments_url == "https://news.ycombinator.com/item?id=40000002"
    assert ask.hn_text is not None
    assert "forty services" in ask.hn_text
    # The hnrss metadata footer is stripped from the captured self-text.
    assert "Comments URL" not in ask.hn_text


def test_github_show_post_keeps_external_url() -> None:
    stories = parse_feed(_load("frontpage.xml"), "frontpage")
    show = next(s for s in stories if s.hn_id == 40000003)
    assert show.url == "https://github.com/example/tinygen"
    assert show.domain == "github.com"


def test_union_dedupes_by_hn_id_and_unions_feeds() -> None:
    front = parse_feed(_load("frontpage.xml"), "frontpage")
    best = parse_feed(_load("best.xml"), "best")

    union = union_feeds({"frontpage": front, "best": best})
    by_id = {s.hn_id: s for s in union}

    # Story 40000001 appears in BOTH feeds -> single entry, feeds unioned.
    assert sum(1 for s in union if s.hn_id == 40000001) == 1
    assert by_id[40000001].feeds == ["best", "frontpage"]

    # The distinct ids across both feeds are all present, no duplicates.
    expected_ids = {40000001, 40000002, 40000003, 40000004}
    assert {s.hn_id for s in union} == expected_ids
    assert len(union) == len(expected_ids)


def test_union_first_nonnull_field_wins_on_merge() -> None:
    front = parse_feed(_load("frontpage.xml"), "frontpage")
    best = parse_feed(_load("best.xml"), "best")
    union = union_feeds({"frontpage": front, "best": best})
    cpu = next(s for s in union if s.hn_id == 40000001)
    # frontpage seen first -> its points (234) are retained; later non-null
    # fields only fill gaps, they don't overwrite.
    assert cpu.points == 234
    # The article url stays the clean (non-tracking) frontpage variant.
    assert cpu.url == "https://example.com/cpu-caches"


def test_union_preserves_first_appearance_order() -> None:
    front = parse_feed(_load("frontpage.xml"), "frontpage")
    best = parse_feed(_load("best.xml"), "best")
    union = union_feeds({"frontpage": front, "best": best})
    ids = [s.hn_id for s in union]
    # frontpage entries come first (in feed order), then best-only entries.
    assert ids[:3] == [40000001, 40000002, 40000003]
    assert ids[-1] == 40000004
