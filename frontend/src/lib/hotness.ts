/**
 * Client-side hotness scoring (interim).
 *
 * The ranking is intentionally NOT baked into data.json — it is computed here at
 * render time so recency keeps decaying live between the backend's refreshes.
 *
 * Formula (HN-style gravity, our starting point):
 *     (points - 1) / (age_hours + 2)^1.8
 *
 * `points` is the max raw score across the story's discussions; `published` is
 * the OLDEST discussion's time (closest to the article's publish date). NOTE:
 * `clout` (the 0..1 normalized, timeless score) is carried in data.json for the
 * eventual cross-source sort but is intentionally NOT used here — it has no age
 * term and would invert this formula. Tuning lives here and only here.
 */
import type { Story } from './types';

/** Gravity exponent on the age term. Higher = faster decay. */
export const GRAVITY = 1.8;
/** Age offset (hours) — softens the curve for very fresh posts. */
export const AGE_OFFSET = 2;

/** Max raw points across a story's discussions (0 if none have a score). */
function maxPoints(story: Story): number {
  let max = 0;
  for (const d of story.discussions) {
    if (typeof d.points === 'number' && Number.isFinite(d.points) && d.points > max) {
      max = d.points;
    }
  }
  return max;
}

/**
 * Hotness score for a single story at instant `now`.
 *
 * Missing/invalid `published` yields a very low (but finite) score so such
 * stories sort to the bottom rather than blowing up the math.
 */
export function score(story: Story, now: number = Date.now()): number {
  const points = maxPoints(story);

  const publishedMs = story.published ? Date.parse(story.published) : NaN;
  if (!Number.isFinite(publishedMs)) {
    return points / 1e6;
  }

  const ageHours = Math.max(0, (now - publishedMs) / 3_600_000);
  return (points - 1) / Math.pow(ageHours + AGE_OFFSET, GRAVITY);
}

/**
 * Return a new array of stories sorted hottest-first.
 *
 * Stable-ish tiebreakers: higher points first, then more recent, then id
 * (so ordering is deterministic across renders and sources).
 */
export function sortByHotness(stories: readonly Story[], now: number = Date.now()): Story[] {
  return [...stories].sort((a, b) => {
    const diff = score(b, now) - score(a, now);
    if (diff !== 0) return diff;

    const pa = maxPoints(a);
    const pb = maxPoints(b);
    if (pb !== pa) return pb - pa;

    const ta = Date.parse(a.published) || 0;
    const tb = Date.parse(b.published) || 0;
    if (tb !== ta) return tb - ta;

    return a.id.localeCompare(b.id);
  });
}
