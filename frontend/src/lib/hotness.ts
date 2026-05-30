/**
 * Client-side hotness scoring.
 *
 * The hotness ranking is intentionally NOT baked into data.json — it is computed
 * here at render time from each story's raw `points` + `published`, so recency
 * keeps decaying live between the backend's ~30-min refreshes.
 *
 * Formula (HN-style gravity, our starting point):
 *     (points - 1) / (age_hours + 2)^1.8
 *
 * Tuning lives here and only here.
 */
import type { Story } from './types';

/** Gravity exponent on the age term. Higher = faster decay. */
export const GRAVITY = 1.8;
/** Age offset (hours) — softens the curve for very fresh posts. */
export const AGE_OFFSET = 2;

/**
 * Hotness score for a single story at instant `now`.
 *
 * Missing/unknown points are treated as 0 and missing/invalid `published`
 * yields a very low (but finite) score so such stories sort to the bottom
 * rather than blowing up the math.
 */
export function score(story: Story, now: number = Date.now()): number {
  const points = typeof story.points === 'number' && Number.isFinite(story.points) ? story.points : 0;

  const publishedMs = story.published ? Date.parse(story.published) : NaN;
  if (!Number.isFinite(publishedMs)) {
    // No usable timestamp: rank purely (and weakly) by points, below all dated stories.
    return points / 1e6;
  }

  const ageHours = Math.max(0, (now - publishedMs) / 3_600_000);
  return (points - 1) / Math.pow(ageHours + AGE_OFFSET, GRAVITY);
}

/**
 * Return a new array of stories sorted hottest-first.
 *
 * Stable-ish tiebreakers: higher points first, then more recent, then hn_id
 * (so ordering is deterministic across renders).
 */
export function sortByHotness(stories: readonly Story[], now: number = Date.now()): Story[] {
  return [...stories].sort((a, b) => {
    const diff = score(b, now) - score(a, now);
    if (diff !== 0) return diff;

    const pa = a.points ?? 0;
    const pb = b.points ?? 0;
    if (pb !== pa) return pb - pa;

    const ta = Date.parse(a.published) || 0;
    const tb = Date.parse(b.published) || 0;
    if (tb !== ta) return tb - ta;

    return a.hn_id - b.hn_id;
  });
}
