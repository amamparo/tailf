"""Tests for models: v2 round-trip serialization, canonical_url, domain_of,
discussion_key."""

from __future__ import annotations

from hackergist.models import (
    SCHEMA_VERSION,
    DataFile,
    Discussion,
    Gist,
    Story,
    canonical_url,
    discussion_key,
    domain_of,
)

from tests.conftest import make_discussion, make_gist, make_story


def test_story_round_trip_preserves_contract_fields() -> None:
    story = make_story(gist=make_gist())
    data = story.to_dict()

    # Exactly the contract keys, nothing extra (self_text must not leak).
    assert set(data) == {
        "id",
        "title",
        "url",
        "domain",
        "image",
        "published",
        "clout",
        "discussions",
        "gist",
    }

    restored = Story.from_dict(data)
    assert restored.to_dict() == data


def test_discussion_round_trip() -> None:
    d = Discussion(source="lobsters", comments_url="https://lobste.rs/s/abc", clout=0.5, points=34)
    assert Discussion.from_dict(d.to_dict()) == d


def test_story_with_two_discussions_round_trips() -> None:
    story = make_story(
        clout=0.9,
        discussions=[
            make_discussion("hn", clout=0.9, points=234),
            make_discussion("lobsters", clout=0.6, points=40),
        ],
        gist=make_gist(),
    )
    data = story.to_dict()
    assert [d["source"] for d in data["discussions"]] == ["hn", "lobsters"]
    assert data["clout"] == 0.9
    assert Story.from_dict(data).to_dict() == data


def test_gist_round_trip() -> None:
    gist = Gist(
        text="Two sentences. Neutral and technical.",
        model="claude-haiku-4-5",
        generated_at="2026-05-29T08:05:00Z",
        kind="readme",
    )
    assert Gist.from_dict(gist.to_dict()) == gist


def test_story_null_url_and_gist_serialize_as_null() -> None:
    story = make_story(id="self:https://lobste.rs/s/x", url=None, domain=None, gist=None)
    data = story.to_dict()
    assert data["url"] is None
    assert data["domain"] is None
    assert data["gist"] is None
    assert Story.from_dict(data).gist is None


def test_self_text_is_not_serialized() -> None:
    story = make_story(url=None, self_text="the self-post body")
    assert "self_text" not in story.to_dict()


def test_datafile_round_trip() -> None:
    df = DataFile(
        stories=[make_story(gist=make_gist()), make_story(id="https://example.com/b", gist=None)],
        generated_at="2026-05-29T12:00:00Z",
    )
    data = df.to_dict()
    assert data["schema_version"] == SCHEMA_VERSION == 2
    assert data["generated_at"] == "2026-05-29T12:00:00Z"
    assert len(data["stories"]) == 2

    restored = DataFile.from_dict(data)
    assert restored.to_dict() == data


def test_datafile_empty() -> None:
    df = DataFile.empty()
    assert df.stories == []
    assert df.schema_version == SCHEMA_VERSION
    assert df.generated_at is None


# --- discussion_key ----------------------------------------------------------


def test_discussion_key_link_post_uses_canonical_url() -> None:
    key = discussion_key(
        "https://ex.com/post?utm_source=x", "https://news.ycombinator.com/item?id=1"
    )
    assert key == "https://ex.com/post"


def test_discussion_key_self_post_is_namespaced() -> None:
    key = discussion_key(None, "https://lobste.rs/s/abc/slug")
    assert key == "self:https://lobste.rs/s/abc/slug"


def test_discussion_key_self_post_never_collides_with_link_post() -> None:
    # An HN post LINKING to a lobste.rs thread, vs. that thread as a self-post.
    link_key = discussion_key("https://lobste.rs/s/abc", "https://news.ycombinator.com/item?id=9")
    self_key = discussion_key(None, "https://lobste.rs/s/abc")
    assert link_key != self_key


# --- canonical_url -----------------------------------------------------------


def test_canonical_url_upgrades_http_and_lowercases_host() -> None:
    assert canonical_url("http://Example.COM/Path") == "https://example.com/Path"


def test_canonical_url_strips_tracking_params() -> None:
    url = "https://example.com/a?utm_source=hn&ref=feed&fbclid=xyz&keep=1"
    assert canonical_url(url) == "https://example.com/a?keep=1"


def test_canonical_url_strips_trailing_slash_and_fragment() -> None:
    assert canonical_url("https://example.com/a/b/#frag") == "https://example.com/a/b"
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
