"""Read / merge / write the single ``data.json`` via the injected FileSystem.

This is the only module that owns the persistence policy:

- :meth:`Store.load` returns the current :class:`DataFile` (empty if absent),
- :func:`merge` produces the next :class:`DataFile` from the current one, the
  freshly-fetched union, and any new gists,
- :meth:`Store.write` serializes and persists it.

The merge rules (the cost/pruning guardrails):

- Keep only stories present in EITHER feed right now (prune the rest).
- REUSE the existing gist by ``hn_id`` — never re-summarize a story that is
  still present and already gisted.
- Refresh ``points`` / ``feeds`` / ``published`` / ``title`` / ``url`` from the
  fresh fetch (those decay/change between runs).
- Stamp ``generated_at`` with the current time.
"""

from __future__ import annotations

import json

from injector import inject

from .config import Config
from .filesystem import FileSystem
from .models import DataFile, Gist, Story, utc_now_iso


class Store:
    """Persistence facade over a :class:`FileSystem` for the data file."""

    @inject
    def __init__(self, config: Config, filesystem: FileSystem) -> None:
        self.config = config
        self.fs = filesystem

    @property
    def key(self) -> str:
        return self.config.data_key

    def load(self) -> DataFile:
        """Return the current data file, or an empty one if none exists."""
        if not self.fs.exists(self.key):
            return DataFile.empty()
        try:
            raw = self.fs.read_text(self.key)
            return DataFile.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, ValueError):
            # A corrupt file should not wedge the pipeline; start fresh.
            return DataFile.empty()

    def write(self, datafile: DataFile) -> None:
        """Serialize and persist the data file (pretty, stable key order)."""
        payload = json.dumps(datafile.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)
        self.fs.write_text(self.key, payload + "\n")


def merge(
    current: DataFile,
    fresh_stories: list[Story],
    new_gists: dict[int, Gist],
    new_images: dict[int, str] | None = None,
) -> DataFile:
    """Produce the next data file from current state + fresh fetch + new gists.

    ``fresh_stories`` is the deduped union from this run (the set of stories
    that should survive). ``new_gists`` maps ``hn_id`` -> a gist produced this
    run for a story that previously had none; ``new_images`` likewise maps
    ``hn_id`` -> a preview image captured this run. Both gist and image are
    REUSED by ``hn_id`` from the current file when not re-produced this run, so
    a story is never re-fetched while it's still present.

    The written ``published`` is guaranteed non-null: if a feed entry has no
    parseable submit time we backfill it with this run's ``generated_at`` so the
    data.json contract (and the frontend's non-null ``published: string`` type)
    always holds. The story still sorts low under the recency-weighted hotness
    score, but the field is never ``null``.
    """
    generated_at = utc_now_iso()
    new_images = new_images or {}

    existing_gists: dict[int, Gist] = {
        s.hn_id: s.gist for s in current.stories if s.gist is not None
    }
    existing_images: dict[int, str] = {
        s.hn_id: s.image for s in current.stories if s.image is not None
    }

    merged: list[Story] = []
    for story in fresh_stories:
        # Gists are STICKY: once a story has one, keep it forever. An existing
        # gist is NEVER overwritten — not by a failed re-gist (null) and not even
        # by a newly produced one (the pipeline doesn't re-gist a story that
        # already has a gist; this is the belt-and-suspenders). Only a story with
        # no existing gist takes a newly produced gist (which may itself be None).
        existing_gist = existing_gists.get(story.hn_id)
        story.gist = existing_gist if existing_gist is not None else new_gists.get(story.hn_id)
        # Prefer a freshly-extracted image, else reuse the stored one.
        story.image = new_images.get(story.hn_id) or existing_images.get(story.hn_id)
        # Guarantee a non-null published timestamp in the written contract.
        if story.published is None:
            story.published = generated_at
        merged.append(story)

    return DataFile(
        stories=merged,
        schema_version=current.schema_version or DataFile.empty().schema_version,
        generated_at=generated_at,
    )
