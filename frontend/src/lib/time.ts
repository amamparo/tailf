/**
 * Compact relative-time formatting, e.g. "3h ago", "just now", "2d ago".
 */

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * Human-friendly "time ago" for an ISO-8601 string, relative to `now`.
 * Returns '' for missing/invalid input so callers can simply skip rendering.
 */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return '';

  const seconds = Math.round((now - then) / 1000);

  // Future timestamps (clock skew between client + backend) read as "just now".
  if (seconds < 45) return 'just now';
  if (seconds < 90) return '1m ago';
  if (seconds < HOUR) return `${Math.round(seconds / MINUTE)}m ago`;
  if (seconds < 90 * MINUTE) return '1h ago';
  if (seconds < DAY) return `${Math.round(seconds / HOUR)}h ago`;
  if (seconds < 2 * DAY) return '1d ago';
  if (seconds < WEEK) return `${Math.round(seconds / DAY)}d ago`;
  if (seconds < MONTH) return `${Math.round(seconds / WEEK)}w ago`;
  if (seconds < YEAR) return `${Math.round(seconds / MONTH)}mo ago`;
  return `${Math.round(seconds / YEAR)}y ago`;
}

/** Full, localized timestamp for a `title=` tooltip on the relative time. */
export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return '';
  return new Date(then).toLocaleString();
}
