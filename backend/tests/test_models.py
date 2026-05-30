"""Tests for models: round-trip serialization, canonical_url, domain_of."""

from __future__ import annotations

from hackergist.models import (
    SCHEMA_VERSION,
    DataFile,
    Gist,
    Story,
    canonical_url,
    domain_of,
    normalize_feeds,
)


def _sample_story(**overrides) -> Story:
    base = {
        "hn_id": 40000001,
        "title": "A deep dive into modern CPU caches",
        "url": "https://example.com/cpu-caches",
        "domain": "example.com",
        "comments_url": "https://news.ycombinator.com/item?id=40000001",
        "points": 234,
        "author": "alice",
        "published": "2026-05-29T08:00:00Z",
        "feeds": ["best", "frontpage"],
        "gist": Gist(
            text="A technical overview of CPU cache hierarchies.",
            model="claude-haiku-4-5",
            generated_at="2026-05-29T08:05:00Z",
            kind="article",
        ),
    }
    base.update(overrides)
    return Story(**base)


def test_story_round_trip_preserves_contract_fields() -> None:
    story = _sample_story()
    data = story.to_dict()

    # Exactly the contract keys, nothing extra (hn_text must not leak).
    assert set(data) == {
        "hn_id",
        "title",
        "url",
        "domain",
        "comments_url",
        "points",
        "author",
        "published",
        "feeds",
        "image",
        "gist",
    }

    restored = Story.from_dict(data)
    assert restored.to_dict() == data


def test_gist_round_trip() -> None:
    gist = Gist(
        text="Two sentences. Neutral and technical.",
        model="claude-haiku-4-5",
        generated_at="2026-05-29T08:05:00Z",
        kind="readme",
    )
    assert Gist.from_dict(gist.to_dict()) == gist


def test_story_null_url_and_gist_serialize_as_null() -> None:
    story = _sample_story(url=None, domain=None, gist=None)
    data = story.to_dict()
    assert data["url"] is None
    assert data["domain"] is None
    assert data["gist"] is None
    assert Story.from_dict(data).gist is None


def test_hn_text_is_not_serialized() -> None:
    story = _sample_story(url=None, hn_text="the self-post body")
    assert "hn_text" not in story.to_dict()


def test_datafile_round_trip() -> None:
    df = DataFile(
        stories=[_sample_story(), _sample_story(hn_id=40000002, gist=None)],
        generated_at="2026-05-29T12:00:00Z",
    )
    data = df.to_dict()
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["generated_at"] == "2026-05-29T12:00:00Z"
    assert len(data["stories"]) == 2

    restored = DataFile.from_dict(data)
    assert restored.to_dict() == data


def test_datafile_empty() -> None:
    df = DataFile.empty()
    assert df.stories == []
    assert df.schema_version == SCHEMA_VERSION
    assert df.generated_at is None


def test_feeds_are_normalized_sorted_and_unique() -> None:
    story = _sample_story(feeds=["frontpage", "best", "frontpage", "bogus"])
    assert story.feeds == ["best", "frontpage"]


def test_normalize_feeds_drops_unknown() -> None:
    assert normalize_feeds(["frontpage", "weird", "best"]) == ["best", "frontpage"]
    assert normalize_feeds([]) == []
    assert normalize_feeds(None) == []


# --- canonical_url -----------------------------------------------------------


def test_canonical_url_upgrades_http_and_lowercases_host() -> None:
    assert canonical_url("http://Example.COM/Path") == "https://example.com/Path"


def test_canonical_url_strips_tracking_params() -> None:
    url = "https://example.com/a?utm_source=hn&ref=feed&fbclid=xyz&keep=1"
    assert canonical_url(url) == "https://example.com/a?keep=1"


def test_canonical_url_strips_trailing_slash_and_fragment() -> None:
    assert canonical_url("https://example.com/a/b/#frag") == "https://example.com/a/b"
    # A bare root slash collapses to no path.
    assert canonical_url("https://example.com/") == "https://example.com"


def test_canonical_url_two_feed_variants_match() -> None:
    a = "https://example.com/cpu-caches"
    b = "https://example.com/cpu-caches?utm_source=hn&ref=feed"
    assert canonical_url(a) == canonical_url(b)


def test_canonical_url_none_and_empty() -> None:
    assert canonical_url(None) is None
    assert canonical_url("") is None


def test_canonical_url_preserves_nondefault_port() -> None:
    assert canonical_url("http://example.com:8080/x") == "https://example.com:8080/x"


# --- domain_of ---------------------------------------------------------------


def test_domain_of_basic() -> None:
    assert domain_of("https://www.example.com/path") == "example.com"
    assert domain_of("https://blog.example.com/path") == "example.com"


def test_domain_of_multi_label_suffix() -> None:
    assert domain_of("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert domain_of("https://shop.foo.com.au/x") == "foo.com.au"


def test_domain_of_none() -> None:
    assert domain_of(None) is None
    assert domain_of("not a url") is None
