"""Data models + URL helpers — the ``data.json`` contract, in code.

These dataclasses are the single source of truth for the JSON the backend
PRODUCES and the frontend CONSUMES. ``to_dict`` / ``from_dict`` round-trip
the contract EXACTLY; do not rename fields without changing both sides.

Contract (schema_version 2)::

    {
      "schema_version": 2,
      "generated_at": "...Z",
      "stories": [
        {
          "id": "https://example.com/post",   // canonical url (link post) or "self:<comments_url>"
          "title": "string",
          "url": "string|null",                // article url; null for self/text posts
          "domain": "string|null",
          "image": "https://...|null",          // og:image / twitter:image, for the card
          "published": "...Z",                  // OLDEST discussion's submit time
          "clout": 0.87,                         // max clout across discussions (0..1)
          "discussions": [                       // >=1; oldest-submit-first, then source
            {
              "source": "hn",                    // "hn" | "lobsters"
              "comments_url": "https://news.ycombinator.com/item?id=...",
              "clout": 0.87,                     // this source's 0..1 normalized score
              "points": 234                      // raw score (may be null)
            }
          ],
          "gist": {
            "text": "...",
            "model": "claude-haiku-4-5",
            "generated_at": "...Z",
            "kind": "article"
          }
        }
      ]
    }

One record is ONE article (or one self-post) with one-or-more ``discussions`` —
the same link posted to both HN and lobste.rs is a single merged record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA_VERSION = 2

#: The kinds of source a gist can be derived from. ``self_text`` covers any
#: self/text post (HN Ask/Show, lobste.rs ``ask``) — it is source-neutral. There
#: is no "title only" kind: a gist is null rather than a bare title restatement.
GistKind = Literal["article", "self_text", "readme", "pdf"]

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
    return to_iso_z(datetime.now(UTC))


def iso_from_epoch(epoch: object) -> str | None:
    """Convert a Unix epoch (seconds) to an ISO-8601 UTC string, or ``None``.

    The HN Firebase API gives ``time`` as integer epoch seconds; the data.json
    contract uses ISO-8601 ``...Z`` strings. Returns ``None`` for missing or
    unparseable input (the merge step backfills a non-null ``published``).
    """
    if epoch is None:
        return None
    try:
        return to_iso_z(datetime.fromtimestamp(int(epoch), tz=UTC))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def to_iso_z(dt: datetime) -> str:
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


def discussion_key(url: str | None, comments_url: str) -> str:
    """The stable record key for a post: groups discussions of the same article.

    Link posts key on their canonical article URL, so the same link from HN and
    lobste.rs merges into one record. Self/text posts (or a link with no usable
    canonical form) key on a ``self:``-namespaced comments URL — so a self-post
    can never collide with a link post whose article URL is that same page.
    """
    if url:
        curl = canonical_url(url)
        if curl is not None:
            return curl
    return f"self:{comments_url}"


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
class Discussion:
    """One community's discussion of a story (a link to its comments + score)."""

    source: str  # "hn" | "lobsters"
    comments_url: str
    clout: float
    points: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "comments_url": self.comments_url,
            "clout": self.clout,
            "points": self.points,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Discussion:
        return cls(
            source=data["source"],
            comments_url=data["comments_url"],
            clout=float(data.get("clout", 0.0)),
            points=data.get("points"),
        )


@dataclass
class Story:
    """One article (or self-post) in the union, with its discussions + gist.

    ``id`` is the stable record key (see :func:`discussion_key`): the canonical
    article URL for link posts, or a ``self:`` comments URL for self-posts. The
    same article from two sources is ONE ``Story`` with two ``discussions``.
    """

    id: str
    title: str
    url: str | None
    domain: str | None
    #: ISO-8601 UTC submit time — the OLDEST discussion's time (closest to the
    #: article's publish date). May be ``None`` in-flight when a feed entry has
    #: no parseable date, but :func:`tailf.store.merge` backfills any
    #: ``None`` with the run's ``generated_at`` BEFORE writing, so the persisted
    #: data.json always carries a non-null ``published``.
    published: str | None
    #: Max clout across ``discussions`` (0..1). Recomputed every run (NOT sticky
    #: like the gist). An input to the eventual cross-source sort.
    clout: float = 0.0
    discussions: list[Discussion] = field(default_factory=list)
    gist: Gist | None = None
    #: Social/preview image URL (og:image / twitter:image) for the card, or
    #: ``None``. Captured during extraction; reused across runs like the gist.
    image: str | None = None
    #: Stored self-post text (HN Ask/Show, lobste.rs ask). NOT serialized to
    #: data.json — used only as gist input during a run.
    self_text: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the exact contract shape (excludes ``self_text``)."""
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "image": self.image,
            "published": self.published,
            "clout": self.clout,
            "discussions": [d.to_dict() for d in self.discussions],
            "gist": self.gist.to_dict() if self.gist is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Story:
        gist_data = data.get("gist")
        return cls(
            id=str(data["id"]),
            title=data["title"],
            url=data.get("url"),
            domain=data.get("domain"),
            published=data.get("published"),
            clout=float(data.get("clout", 0.0)),
            discussions=[Discussion.from_dict(d) for d in data.get("discussions", [])],
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
