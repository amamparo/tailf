/**
 * Loads the single data.json that backs the whole app.
 *
 * In production this is served same-origin by CloudFront (from S3, next to the
 * static site). In dev a Vite middleware (see vite.config.ts) serves
 * ../.data/data.json at the same path. The service worker caches it NetworkFirst.
 */
import type { DataFile, Story } from './types';

export class DataError extends Error {
  constructor(
    message: string,
    readonly cause?: unknown
  ) {
    super(message);
    this.name = 'DataError';
  }
}

const DATA_URL = '/data.json';

/** The schema this frontend understands. A cached older file is rejected (see
 * loadData) rather than silently rendering an empty feed. */
const SCHEMA_VERSION = 2;

function isStory(value: unknown): value is Story {
  if (typeof value !== 'object' || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    typeof s.id === 'string' &&
    typeof s.title === 'string' &&
    Array.isArray(s.discussions) &&
    s.discussions.length > 0
  );
}

function isDataFile(value: unknown): value is DataFile {
  if (typeof value !== 'object' || value === null) return false;
  const d = value as Record<string, unknown>;
  return d.schema_version === SCHEMA_VERSION && Array.isArray(d.stories);
}

/**
 * Fetch + validate data.json. Throws {@link DataError} on network / parse /
 * shape failures so callers can render a clean error state.
 */
export async function loadData(fetchFn: typeof fetch = fetch): Promise<DataFile> {
  let res: Response;
  try {
    res = await fetchFn(DATA_URL, { headers: { accept: 'application/json' } });
  } catch (err) {
    throw new DataError('Could not reach the gist feed. Check your connection.', err);
  }

  if (!res.ok) {
    throw new DataError(`Gist feed returned ${res.status}.`);
  }

  let json: unknown;
  try {
    json = await res.json();
  } catch (err) {
    throw new DataError('The gist feed was not valid JSON.', err);
  }

  if (!isDataFile(json)) {
    throw new DataError('The gist feed had an unexpected shape.');
  }

  // Defensively drop anything that doesn't look like a Story rather than crashing render.
  const total = json.stories.length;
  json.stories = json.stories.filter(isStory);
  const dropped = total - json.stories.length;
  if (dropped > 0) {
    // Surface (rather than silently swallow) a likely backend contract break,
    // e.g. a renamed field, so it's noticed during local dev instead of stories vanishing.
    console.warn(
      `[tailf] Dropped ${dropped} of ${total} stories from data.json that did not match the expected Story shape. ` +
        'This usually means the backend data contract changed.'
    );
  }
  return json;
}
