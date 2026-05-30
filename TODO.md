# TODO — hackergist

*hackergist.dev — a public site that aggregates top Hacker News posts, each with a 1–2 sentence "gist." Titles link straight to the source URL; a secondary link goes to the HN discussion.*

## Problem
Skimming Hacker News means opening a dozen tabs just to learn what each link *is*. hackergist shows the day's top HN stories, each with a one- or two-sentence gist of the **linked content**, so you can decide what's worth a click without the round trip.

## Sources
- Frontpage — https://hnrss.org/frontpage
- Best — https://hnrss.org/best
- hnrss supports query params worth using: `?points=N` (threshold), `?count=N`, `&description=0`. Each item's `<link>` is the article URL; the HN comments URL is provided separately in the item.

## Core flow
fetch both feeds → dedupe (frontpage ∩ best overlap) → for each *new* story: fetch the linked page, extract main text, summarize to ≤2 sentences via an LLM → cache by HN id / URL → render a fast public page.

## Open decisions (resolve early — these shape everything)
1. **Stack** — static-site-generated (a scheduled builder emits JSON/HTML, hosted on Cloudflare Pages / S3+CDN / Vercel) vs. a small dynamic server. Leaning static for speed + cost. Frontend: plain HTML or Svelte/SvelteKit?
2. **LLM for gists** — which model + provider (cost / latency / quality), the prompt, and per-article token budget. Key lives backend-only, never client-side.
3. **Article extraction** — Readability / trafilatura-style main-text extraction. Non-article links: GitHub → README, PDF → extracted text, video/image/paywalled → fall back to title + HN snippet, or skip the gist.
4. **Text vs link posts** — Ask HN / Show HN / text posts whose link *is* the HN item: gist the HN text instead of an external page.
5. **Refresh cadence + size** — how often the builder runs (e.g., every 15–30 min) and how many stories to show (top N).
6. **Gist spec** — summarize the *linked content* (not the HN thread); neutral, factual, technical tone; hard 2-sentence cap.

## Milestones

### M0 — Scaffolding
- [x] Create repo
- [ ] Pick stack; wire up `hackergist.dev` DNS + TLS later (NB: `.dev` is HSTS-preloaded → HTTPS required)
- [ ] Config: feed URLs, top-N, refresh interval, model name (key via env/secret)

### M1 — Ingest
- [ ] Fetch both hnrss feeds (concurrent); conditional GET, timeouts, retries
- [ ] Parse items → model `{hn_id, title, url, comments_url, points, author, published}`
- [ ] Dedupe across frontpage/best by HN id (and by canonical URL)

### M2 — Gist pipeline (the meat)
- [ ] Fetch linked page; extract main text; truncate to token budget
- [ ] Summarize to ≤2 sentences (neutral, technical); store gist + model + timestamp
- [ ] Fallbacks: GitHub → README, PDF → text, video/paywall/dead → title-only or skip
- [ ] Never re-summarize cached URLs; only backfill new stories

### M3 — Storage / cache
- [ ] Persist stories + gists keyed by HN id / URL (SQLite or a KV/JSON store)
- [ ] Keep fetch metadata (etag, last-seen) for politeness

### M4 — Public UI
- [ ] List view: **title → source URL**, gist underneath, meta (domain, points, HN-comments link, time)
- [ ] Frontpage vs Best sections/toggle; per-domain favicon (optional)
- [ ] Fast, minimal, mobile-friendly, dark mode
- [ ] Also emit a gisted RSS/Atom feed (nice meta touch)

### M5 — Deploy
- [ ] Scheduled builder (cron / Cloudflare Worker / Lambda + EventBridge)
- [ ] Host static output behind a CDN; wire up hackergist.dev + TLS
- [ ] Basic logging: what got gisted, what failed

## Edge cases
- Same story on both feeds (dedupe)
- Non-HTML links (PDF, video, image, GitHub, tweets), paywalled, or dead URLs
- Ask / Show / text HN posts with no external URL
- Very long articles (truncate before summarizing); non-English content
- LLM failure / rate limits → graceful fallback to HN title + snippet
- Canonical-URL matching (tracking params, http vs https, trailing slashes)
- Cost guardrails on summarization (cap per run; cache aggressively)

## Etiquette / legal
- Link directly to source (as designed) + link to the HN discussion
- Polite fetching (conditional GET, rate-limit, identify UA); cache to avoid re-hitting sites
- Short, factual gists of third-party content; attribute source domain clearly; don't republish full article text
- Respect hnrss / HN usage norms

## Nice-to-haves (later)
- [ ] Search / filter by domain or topic
- [ ] Topic tags or clustering
- [ ] "Why it's trending" (points / velocity)
- [ ] Save / read-later; keyboard nav
- [ ] Gisted email digest

## Decisions log
- _(record resolved decisions here as we iterate)_
