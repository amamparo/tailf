/**
 * Client-side feed ordering — even cross-source interleaving.
 *
 * The ranking is NOT baked into data.json; it's computed here at render time so
 * recency keeps decaying live between the backend's refreshes (a `now` clock
 * ticks every 60s on the page).
 *
 * The problem with sorting on raw points: HN scores run to the hundreds while
 * lobste.rs runs to the tens, so a global points sort buries lobste.rs at the
 * bottom. The fix, with no per-source weights or factors:
 *
 *   1. Score each story by a recency-aware hotness — (points-1)/(age_h+2)^1.8 —
 *      which is fine WITHIN a source (one consistent point scale).
 *   2. Rank each story against its OWN source and take its percentile (0..1).
 *      A story on both sources takes the max percentile across them.
 *   3. Sort by that percentile. Because every source's percentiles span [0,1]
 *      identically, the feed alternates sources by rank — evenly — instead of by
 *      absolute points.
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
 * Recency-aware hotness for a single story (used to rank WITHIN a source, where
 * the point scale is consistent). Missing/invalid `published` sorts to the
 * bottom rather than blowing up the math.
 */
export function hotness(story: Story, now: number = Date.now()): number {
  const points = maxPoints(story);
  const publishedMs = story.published ? Date.parse(story.published) : NaN;
  if (!Number.isFinite(publishedMs)) return points / 1e6;
  const ageHours = Math.max(0, (now - publishedMs) / 3_600_000);
  return (points - 1) / Math.pow(ageHours + AGE_OFFSET, GRAVITY);
}

/** Distinct sources a story was discussed on. */
function sourcesOf(story: Story): string[] {
  const set = new Set<string>();
  for (const d of story.discussions) set.add(d.source);
  return [...set];
}

/** Percentile of `value` within the ascending-sorted `arr` (ties averaged). */
function percentile(arr: number[], value: number): number {
  const n = arr.length;
  if (n <= 1) return 1;
  let below = 0;
  let equal = 0;
  for (const x of arr) {
    if (x < value) below++;
    else if (x === value) equal++;
  }
  return (below + (equal - 1) / 2) / (n - 1);
}

/**
 * Return a new array of stories sorted hottest-first, interleaving sources
 * evenly (see the module comment). Tiebreak: higher raw hotness, then id.
 */
export function sortByHotness(stories: readonly Story[], now: number = Date.now()): Story[] {
  const hot = new Map<Story, number>();
  for (const s of stories) hot.set(s, hotness(s, now));

  // Each source's hotness values (a story on both sources joins both lists).
  const bySource = new Map<string, number[]>();
  for (const s of stories) {
    for (const src of sourcesOf(s)) {
      const arr = bySource.get(src);
      if (arr) arr.push(hot.get(s)!);
      else bySource.set(src, [hot.get(s)!]);
    }
  }
  for (const arr of bySource.values()) arr.sort((a, b) => a - b);

  // Sort key: best percentile rank across the story's sources.
  const keyOf = new Map<Story, number>();
  for (const s of stories) {
    const h = hot.get(s)!;
    keyOf.set(s, Math.max(...sourcesOf(s).map((src) => percentile(bySource.get(src)!, h))));
  }

  return [...stories].sort((a, b) => {
    const dk = keyOf.get(b)! - keyOf.get(a)!;
    if (dk !== 0) return dk;
    const dh = hot.get(b)! - hot.get(a)!;
    if (dh !== 0) return dh;
    return a.id.localeCompare(b.id);
  });
}
