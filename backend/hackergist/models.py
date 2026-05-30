"""Data models + URL helpers — the ``data.json`` contract, in code.

These dataclasses are the single source of truth for the JSON the backend
PRODUCES and the frontend CONSUMES. ``to_dict`` / ``from_dict`` round-trip
the contract EXACTLY; do not rename fields without changing both sides.

Contract (schema_version 1)::

    {
      "schema_version": 1,
      "generated_at": "...Z",
      "stories": [
        {
          "hn_id": 40000001,
          "title": "string",
          "url": "string|null",
          "domain": "string|null",
          "comments_url": "https://news.ycombinator.com/item?id=...",
          "points": 234,
          "author": "string|null",
          "published": "...Z",          // non-null in the written file (see below)
          "feeds": ["frontpage", "best"],
          "image": "https://...|null",  // og:image / twitter:image, for the card
          "gist": {
            "text": "...",
            "model": "claude-haiku-4-5",
            "generated_at": "...Z",
            "kind": "article"
          }
        }
      ]
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA_VERSION = 1

#: Allowed feed names, in canonical (sorted) order.
FEED_NAMES = ("best", "frontpage")

#: The kinds of source a gist can be derived from.
GistKind = Literal["article", "hn_text", "readme", "pdf", "title_only"]

# Tracking / referral query params stripped during URL canonicalization.
# utm_* is handled by prefix; these are exact-match strips.
_TRACKING_PARAMS = frozenset(
    {
        "ref",
        "ref_src",
        "ref_url",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "spm",
        "cmpid",
        "_hsenc",
        "_hsmi",
        "source",
    }
)


def utc_now_iso() -> str:
    """Return the current time as an ISO-8601 UTC string with a ``Z`` suffix."""
    return _to_iso_z(datetime.now(UTC))


def _to_iso_z(dt: datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, second precision)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMS


def canonical_url(url: str | None) -> str | None:
    """Return a canonical form of ``url`` for dedupe and comparison.

    Normalizations:

    - lowercase the host (scheme/host are case-insensitive),
    - upgrade ``http`` -> ``https``,
    - drop common tracking params (``utm_*``, ``ref``, ``fbclid``, ...),
    - strip a trailing slash from the path (but keep a bare ``/`` -> ``""``),
    - drop the fragment.

    Returns ``None`` for falsy input. The path case is preserved (paths can be
    case-sensitive). This is used only for matching, never for display.
    """
    if not url:
        return None

    parts = urlsplit(url.strip())
    if not parts.netloc:
        # Not an absolute URL we can meaningfully canonicalize.
        return url.strip() or None

    scheme = "https" if parts.scheme in ("", "http", "https") else parts.scheme
    host = parts.hostname.lower() if parts.hostname else ""

    # Preserve a non-default port if present.
    netloc = host
    if parts.port is not None:
        default_port = {"http": 80, "https": 443}.get(scheme)
        if parts.port != default_port:
            netloc = f"{host}:{parts.port}"

    path = parts.path
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))


def domain_of(url: str | None) -> str | None:
    """Return the registrable domain (``eTLD+1``) for ``url``, or ``None``.

    Strips a leading ``www.`` and collapses a small set of common multi-label
    public suffixes (``co.uk``, ``com.au``, ...) so that, e.g.,
    ``https://www.bbc.co.uk/news`` -> ``bbc.co.uk``. This is a pragmatic
    heuristic rather than a full Public Suffix List lookup, which is plenty
    for displaying a source badge.
    """
    if not url:
        return None

    host = urlsplit(url.strip()).hostname
    if not host:
        return None

    host = host.lower()
    if host.startswith("www."):
        host = host[4:]

    labels = host.split(".")
    if len(labels) <= 2:
        return host or None

    # Common two-label public suffixes where the registrable domain is eTLD+1
    # at three labels (e.g. bbc.co.uk, foo.com.au).
    two_label_suffixes = {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "co.jp",
        "com.br",
        "co.in",
        "com.cn",
        "co.za",
    }
    last_two = ".".join(labels[-2:])
    if last_two in two_label_suffixes:
        return ".".join(labels[-3:])
    return last_two


@dataclass
class Gist:
    """An AI-generated gist of a story's source content."""

    text: str
    model: str
    generated_at: str
    kind: GistKind

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "generated_at": self.generated_at,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Gist:
        return cls(
            text=data["text"],
            model=data["model"],
            generated_at=data["generated_at"],
            kind=data["kind"],
        )


@dataclass
class Story:
    """A single HN story in the union, with optional gist.

    ``feeds`` is always kept as a sorted, de-duplicated list of feed names so
    the serialized form is deterministic (good for caching and diffing).
    """

    hn_id: int
    title: str
    url: str | None
    domain: str | None
    comments_url: str
    points: int | None
    author: str | None
    #: ISO-8601 UTC submit time. May be ``None`` in-flight when a feed entry has
    #: no parseable date, but :func:`hackergist.store.merge` backfills any
    #: ``None`` with the run's ``generated_at`` BEFORE writing, so the persisted
    #: data.json always carries a non-null ``published`` (matching the frontend
    #: ``published: string`` type). Never assume non-null before that merge step.
    published: str | None
    feeds: list[str] = field(default_factory=list)
    gist: Gist | None = None
    #: Social/preview image URL (og:image / twitter:image) for the card, or
    #: ``None``. Captured during extraction; reused across runs like the gist.
    image: str | None = None
    #: Stored HN self-post text (Ask/Show/text posts). NOT serialized to
    #: data.json — used only as gist input during a run.
    hn_text: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.feeds = normalize_feeds(self.feeds)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the exact contract shape (excludes ``hn_text``)."""
        return {
            "hn_id": self.hn_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "comments_url": self.comments_url,
            "points": self.points,
            "author": self.author,
            "published": self.published,
            "feeds": list(self.feeds),
            "image": self.image,
            "gist": self.gist.to_dict() if self.gist is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Story:
        gist_data = data.get("gist")
        return cls(
            hn_id=int(data["hn_id"]),
            title=data["title"],
            url=data.get("url"),
            domain=data.get("domain"),
            comments_url=data["comments_url"],
            points=data.get("points"),
            author=data.get("author"),
            published=data.get("published"),
            feeds=list(data.get("feeds", [])),
            image=data.get("image"),
            gist=Gist.from_dict(gist_data) if gist_data else None,
        )


@dataclass
class DataFile:
    """The whole persisted document: schema version, timestamp, and stories."""

    stories: list[Story] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    generated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "stories": [s.to_dict() for s in self.stories],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataFile:
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            generated_at=data.get("generated_at"),
            stories=[Story.from_dict(s) for s in data.get("stories", [])],
        )

    @classmethod
    def empty(cls) -> DataFile:
        """An empty data file (used when no prior file exists in storage)."""
        return cls(stories=[], schema_version=SCHEMA_VERSION, generated_at=None)


def normalize_feeds(feeds: object) -> list[str]:
    """Return a sorted, de-duplicated list of known feed names."""
    if not feeds:
        return []
    unique = {str(f) for f in feeds if str(f) in FEED_NAMES}
    return sorted(unique)
