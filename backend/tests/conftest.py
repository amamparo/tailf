"""Shared test factories for the v2 (multi-source) contract."""

from __future__ import annotations

from tailf.models import Discussion, Gist, Story
from tailf.sources import Post

_UNSET = object()


def make_gist(text: str = "g", kind: str = "article") -> Gist:
    return Gist(text=text, model="claude-haiku-4-5", generated_at="2026-05-29T08:05:00Z", kind=kind)


def make_discussion(
    source: str = "hn",
    *,
    comments_url: str | None = None,
    clout: float = 1.0,
    points: int | None = 100,
    title: str = "A story",
    published: str | None = "2026-05-29T08:00:00Z",
) -> Discussion:
    if comments_url is None:
        comments_url = (
            f"https://news.ycombinator.com/item?id={points}"
            if source == "hn"
            else f"https://lobste.rs/s/{source}{points}"
        )
    return Discussion(
        source=source,
        comments_url=comments_url,
        clout=clout,
        points=points,
        title=title,
        published=published,
    )


def make_story(
    id: str = "https://example.com/a",
    *,
    title: str | None = None,
    url: object = _UNSET,
    domain: str = "example.com",
    published: str | None = "2026-05-29T08:00:00Z",
    clout: float = 1.0,
    points: int | None = 100,
    discussions: list[Discussion] | None = None,
    gist: Gist | None = None,
    image: str | None = None,
    self_text: str | None = None,
) -> Story:
    if url is _UNSET:
        url = None if id.startswith("self:") else id
    resolved_title = title or f"Story {id}"
    if discussions is None:
        # Default discussion's title matches the story so resolve_title() in the
        # merge is idempotent (a single source trivially "agrees" with itself).
        discussions = [
            make_discussion(
                "hn", clout=clout, points=points, title=resolved_title, published=published
            )
        ]
    return Story(
        id=id,
        title=resolved_title,
        url=url,  # type: ignore[arg-type]
        domain=domain if url else None,
        published=published,
        clout=clout,
        discussions=discussions,
        gist=gist,
        image=image,
        self_text=self_text,
    )


def make_post(
    source: str = "hn",
    *,
    title: str = "A story",
    link: str | None = "https://example.com/a",
    comments_url: str | None = None,
    published: str | None = "2026-05-29T08:00:00Z",
    points: int | None = 100,
    clout: float = 1.0,
    self_text: str | None = None,
) -> Post:
    if comments_url is None:
        comments_url = (
            f"https://news.ycombinator.com/item?id={points}"
            if source == "hn"
            else f"https://lobste.rs/s/{source}{points}"
        )
    return Post(
        title=title,
        link=link,
        comments_url=comments_url,
        source=source,  # type: ignore[arg-type]
        published=published,
        points=points,
        self_text=self_text,
        clout=clout,
    )
