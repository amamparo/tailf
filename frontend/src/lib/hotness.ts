/**
 * Client-side feed ordering — even cross-source interleaving, with a boost for
 * cross-posted stories.
 *
 * The ranking is NOT baked into data.json; it's computed here at render time so
 * recency keeps decaying live between the backend's refreshes (a `now` clock
 * ticks every 60s on the page).
 *
 * The problem with sorting on raw points: HN scores run to the hundreds while
 * lobste.rs runs to the tens, so a global points sort buries lobste.rs at the
 * bottom. The fix, with no per-source weights or factors:
 *
 *   1. Score each DISCUSSION by a recency-aware hotness — (points-1)/(age_h+2)^1.8
 *      — using that source's OWN points and OWN submit time (one consistent
 *      point/time scale within a source).
 *   2. Rank each discussion against its OWN source and take its percentile
 *      (0..1). A cross-posted story has one discussion per source, so it gets
 *      one percentile per source; those are COMBINED via noisy-OR (not max), so
 *      a story on both HN and lobste.rs is lifted above either rank alone —
 *      appearing on both is a significance signal.
 *   3. Sort by that combined rank. Single-source stories keep their plain
 *      percentile, so each source's standings still span [0,1] and interleave
 *      evenly; cross-posted stories float up through that interleave.
 */
import type { Discussion, Story } from './types';

/** Gravity exponent on the age term. Higher = faster decay. */
export const GRAVITY = 1.8;
/** Age offset (hours) — softens the curve for very fresh posts. */
export const AGE_OFFSET = 2;

/**
 * Recency-aware hotness for a SINGLE discussion, from that source's own points
 * and own submit time. Falls back to the story's (oldest) `published` when a
 * discussion has no time of its own — e.g. older cached data.json that predates
 * per-discussion timestamps. Missing/invalid time sorts to the bottom rather
 * than blowing up the math.
 */
function discussionHotness(d: Discussion, storyPublished: string | null, now: number): number {
  const points = typeof d.points === 'number' && Number.isFinite(d.points) ? d.points : 0;
  const iso = d.published ?? storyPublished;
  const publishedMs = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(publishedMs)) return points / 1e6;
  const ageHours = Math.max(0, (now - publishedMs) / 3_600_000);
  return (points - 1) / Math.pow(ageHours + AGE_OFFSET, GRAVITY);
}

/** A story's hotness = the hottest of its discussions (used for the tiebreak). */
export function hotness(story: Story, now: number = Date.now()): number {
  let max = -Infinity;
  for (const d of story.discussions) {
    const h = discussionHotness(d, story.published, now);
    if (h > max) max = h;
  }
  return Number.isFinite(max) ? max : 0;
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
 * Combine a story's per-source percentiles into one sort key via noisy-OR:
 * `1 - ∏(1 - pₛ)`. One source → unchanged (returns `p`); multiple sources →
 * strictly greater than the max (a story strong on both HN and lobste.rs
 * approaches 1) while staying within [0,1]. This is the "cross-posted ⇒ more
 * significant" weighting: it rewards corroboration across sources without a
 * hand-tuned bonus, and degrades to the old single-source behavior otherwise.
 */
function combineRanks(percentiles: number[]): number {
  return percentiles.reduce((combined, p) => 1 - (1 - combined) * (1 - p), 0);
}

/**
 * Return a new array of stories sorted hottest-first, interleaving sources
 * evenly with cross-posts lifted (see the module comment). Tiebreak: higher raw
 * hotness, then id.
 */
export function sortByHotness(stories: readonly Story[], now: number = Date.now()): Story[] {
  // Per-discussion hotness grouped by source (for percentile ranking), plus a
  // per-story max (for the tiebreak).
  const bySource = new Map<string, number[]>();
  const perStoryDiscussions = new Map<Story, { source: string; h: number }[]>();
  const storyMax = new Map<Story, number>();
  for (const s of stories) {
    const entries: { source: string; h: number }[] = [];
    let max = -Infinity;
    for (const d of s.discussions) {
      const h = discussionHotness(d, s.published, now);
      entries.push({ source: d.source, h });
      if (h > max) max = h;
      const list = bySource.get(d.source);
      if (list) list.push(h);
      else bySource.set(d.source, [h]);
    }
    perStoryDiscussions.set(s, entries);
    storyMax.set(s, Number.isFinite(max) ? max : 0);
  }
  for (const list of bySource.values()) list.sort((a, b) => a - b);

  // Sort key: each discussion's percentile WITHIN its own source, combined
  // across the story's sources via noisy-OR (single-source unchanged; a
  // cross-post is lifted above either rank alone — see combineRanks).
  const keyOf = new Map<Story, number>();
  for (const s of stories) {
    const ranks = perStoryDiscussions
      .get(s)!
      .map(({ source, h }) => percentile(bySource.get(source)!, h));
    keyOf.set(s, combineRanks(ranks));
  }

  return [...stories].sort((a, b) => {
    const dk = keyOf.get(b)! - keyOf.get(a)!;
    if (dk !== 0) return dk;
    const dh = storyMax.get(b)! - storyMax.get(a)!;
    if (dh !== 0) return dh;
    return a.id.localeCompare(b.id);
  });
}
