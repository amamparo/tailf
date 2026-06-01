/**
 * TypeScript mirror of the `data.json` contract (schema_version 2).
 *
 * THIS MUST STAY FIELD-FOR-FIELD IDENTICAL to what the backend pipeline writes
 * (backend/hackergist/models.py). data.json is the entire data layer: the union
 * of all sources (Hacker News + lobste.rs), fetched same-origin by this frontend.
 *
 * One record is ONE article (or one self-post) with one-or-more `discussions` —
 * the same link posted to both sources is a single merged card.
 */

/** Which community a discussion lives on. */
export type Source = 'hn' | 'lobsters';

/** How a gist was produced (drives the source of the summary text). */
export type GistKind = 'article' | 'self_text' | 'readme' | 'pdf' | 'title_only';

export interface Gist {
  /** <= 2 sentences, neutral/technical. */
  text: string;
  /** e.g. "claude-haiku-4-5". */
  model: string;
  /** ISO-8601 UTC timestamp the gist was generated. */
  generated_at: string;
  kind: GistKind;
}

/** One community's discussion of a story: a link to its comments + its score. */
export interface Discussion {
  source: Source;
  /** The discussion permalink (HN item / lobste.rs /s/{short_id}). */
  comments_url: string;
  /** This source's 0..1 min-max-normalized score (timeless; popularity only). */
  clout: number;
  /** Raw community score; may be null when the source didn't expose one. */
  points: number | null;
}

export interface Story {
  /** Stable record key: canonical article url, or "self:<comments_url>". */
  id: string;
  title: string;
  /** Article URL; null for self/text posts with no external link. */
  url: string | null;
  /** Registrable domain derived from `url`; null if no url. */
  domain: string | null;
  /** Social/preview image (og:image / twitter:image); null if none found. */
  image: string | null;
  /** ISO-8601 UTC submit time — the OLDEST discussion's (closest to publish). */
  published: string;
  /** Max clout across `discussions` (0..1). */
  clout: number;
  /** >=1 discussions, ordered oldest-submit-first then source. */
  discussions: Discussion[];
  /** null when the source couldn't be read; such stories are hidden in the UI. */
  gist: Gist | null;
}

export interface DataFile {
  /** Always 2 for this frontend; a mismatch is rejected (see data.ts). */
  schema_version: number;
  /** ISO-8601 UTC, when this file was written. */
  generated_at: string;
  stories: Story[];
}
