/**
 * TypeScript mirror of the `data.json` contract.
 *
 * THIS MUST STAY FIELD-FOR-FIELD IDENTICAL to what the backend pipeline writes
 * (backend/hackergist/store.py + models.py). data.json is the entire data layer:
 * the union of the two parameterless hnrss feeds (frontpage + best), fetched
 * same-origin by this frontend.
 */

/** Which hnrss feed(s) a story appeared in. */
export type Feed = 'frontpage' | 'best';

/** How a gist was produced (drives the source of the summary text). */
export type GistKind = 'article' | 'hn_text' | 'readme' | 'pdf' | 'title_only';

export interface Gist {
  /** <= 2 sentences, neutral/technical. */
  text: string;
  /** e.g. "claude-haiku-4-5". */
  model: string;
  /** ISO-8601 UTC timestamp the gist was generated. */
  generated_at: string;
  kind: GistKind;
}

export interface Story {
  /** HN item id — the dedupe key. */
  hn_id: number;
  title: string;
  /** Article URL; null for Ask/Show/text posts with no external link. */
  url: string | null;
  /** Registrable domain derived from `url`; null if no url. */
  domain: string | null;
  /** https://news.ycombinator.com/item?id=<hn_id> */
  comments_url: string;
  /** HN points; may be 0 or null if unknown. */
  points: number | null;
  author: string | null;
  /** ISO-8601 UTC submit time. */
  published: string;
  /** Subset of ["frontpage", "best"]; which feeds it appeared in. */
  feeds: Feed[];
  /** Social/preview image (og:image / twitter:image); null if none found. */
  image: string | null;
  /** null when the source couldn't be read; such stories are hidden in the UI. */
  gist: Gist | null;
}

export interface DataFile {
  schema_version: number;
  /** ISO-8601 UTC, when this file was written. */
  generated_at: string;
  stories: Story[];
}
