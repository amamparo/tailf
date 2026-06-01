# TODO — Incorporate lobste.rs as a second source

## Goal

Show lobste.rs stories alongside Hacker News in the single `data.json`, each with the same AI gist of the linked article. Introduce a clean **`FeedSource`** abstraction so HN and lobste.rs (and any future source) are interchangeable producers of a common **`Post`**, and the pipeline stops being HN-shaped.

> **Verified against the codebase (gotchas the inherited docs get wrong):**
> - `frontend/static/data.json` **does not exist**; the only sample is [.data/data.json](.data/data.json). A sample must NOT live under `static/` — [vite.config.ts:22-25](frontend/vite.config.ts#L22-L25) warns it would be copied to `build/` and deployed to S3, clobbering the Lambda-written `data.json`. (CLAUDE.md:66 + the vite.config.ts:18 comment claim a static fallback that isn't wired — flag for cleanup.)
> - `feedparser` is installed (6.0.12) but **only transitively** — it is NOT in [pyproject.toml](pyproject.toml) or the [Dockerfile](backend/Dockerfile). CLAUDE.md lists it as a main dep; that's stale. Phase 2 needs it, so declare it explicitly.

## Architecture decision

Today the pipeline is HN-centric end to end: `fetch_feeds` → `union_feeds` → `list[Story]` keyed on an **`int` `hn_id`**, the sole anchor for dedupe, gist reuse, image reuse, and pruning ([fetch.py:208](backend/tailf/fetch.py#L208), [store.py:84-101](backend/tailf/store.py#L84-L101), [pipeline.py:194](backend/tailf/pipeline.py#L194)).

Split into two layers:

- **Source layer (new):** a `FeedSource` interface with `get_posts() -> list[Post]`, implemented by `HackerNewsSource` and `LobstersSource`. Each source owns its own fetching/quirks and computes **`clout`** over *its own* batch.
- **Pipeline layer (refactored):** collects `Post`s from all enabled sources, **merges across sources by canonical URL** into persisted records, enriches each with a gist + image, and writes `data.json`.

`Post` is the lean source contract; the persisted record (still `Story`) is the enriched/merged thing the frontend reads.

> **Cost invariant — a post is scraped + summarized at most once.** Two mechanisms guarantee it:
> 1. **Within a run:** cross-source merge (`merge_posts`, by canonical URL) runs **before** the scrape/gist phase, so the unit handed to extraction is the single merged `Story` — an article on both HN and lobste.rs is fetched + summarized **once**, not per source. (Same for HN's own top/best overlap, deduped at the `Post` level inside `HackerNewsSource`.)
> 2. **Across runs:** the gist + image are **sticky, reused by `story_key`** ([store.merge](backend/tailf/store.py#L60)); `_stories_needing_gist` returns only stories with no existing gist, so a story already gisted is never re-fetched. The render fallback is keyed the same way.
>
> Scrape (article fetch + text/image extraction) and summarize happen in the same per-`Story` pass ([_gist_static](backend/tailf/pipeline.py#L127)), so "not scraped twice" and "not summarized twice" are the same guarantee.

### `Post` — the source contract

```python
@dataclass
class Post:
    title: str
    link: str | None       # article URL; None for self/text posts (Ask/Show)
    comments_url: str        # the discussion permalink (HN item / lobste.rs /s/{id})
    clout: float             # 0..1, min-max normalized over THIS source's batch
    # Proposed additions (D3) — the pipeline needs these and both feeds carry them:
    source: str              # "hn" | "lobsters"
    published: str | None    # ISO-8601 UTC — card "x ago" + interim recency sort
    points: int | None       # RAW points pre-normalization — load-bearing for the interim sort
    self_text: str | None    # body of a self/text post, to gist when there's no link (not serialized)
```

**`clout`** = `(points - min) / (max - min)` across the posts that source returned this run. It is *strictly a function of points*; **the feed sort is a separate, later task** (see Deferred). Two properties to state plainly so nobody is surprised:

- **clout is recomputed every run and is NOT sticky** (unlike the gist, which is reused by key). A post's persisted clout is overwritten each run as the batch min/max shift. That's fine because the sort is deferred and clout is derived, not authored.
- **Cross-source clout comparability is an approximation.** min-max over a ~25-post lobste.rs batch vs a ~55-post HN batch (and a single 800-point HN post compresses everyone toward 0) means "top of lobste.rs" and "top of HN" land on different distributions. `max`-clout-wins on merge is a pragmatic placeholder, revisited when the sort lands.

### `FeedSource` — the interface

```python
class FeedSource(ABC):
    name: str  # "hn" | "lobsters" — for the source badge + per-source run counts
    @abstractmethod
    def get_posts(self) -> list[Post]: ...
```

`get_posts()` fetches everything, derives `points`, then computes `clout` over the batch as the last step (it needs the batch min/max).

### New `data.json` contract (schema_version 2)

The **merged-card** decision (same article on HN *and* lobste.rs → one card, both discussion links) makes a record *one article, one-or-more discussions*:

```jsonc
{
  "schema_version": 2,
  "generated_at": "...Z",
  "stories": [
    {
      "id": "https://example.com/post",      // stable key: canonical url (link posts) or "self:<comments_url>" (self posts)
      "title": "string",
      "url": "string|null",                   // article url; null for self/text posts
      "domain": "string|null",
      "image": "https://...|null",
      "published": "...Z",                     // OLDEST discussion's submit time (D5) — closest to the article's publish date
      "clout": 0.87,                            // max clout across discussions (recomputed each run)
      "discussions": [                          // DETERMINISTIC order: oldest submit time first, then source asc
        { "source": "hn",       "comments_url": "https://news.ycombinator.com/item?id=...", "clout": 0.87, "points": 234 },
        { "source": "lobsters", "comments_url": "https://lobste.rs/s/aahxxs/...",          "clout": 0.62, "points": 34  }
      ],
      "gist": { "text": "...", "model": "claude-haiku-4-5", "generated_at": "...Z", "kind": "article" }
    }
  ]
}
```

Changes from v1: `hn_id`(int) → `id`(string); `points`/`comments_url` move into `discussions[]`; **`feeds` dropped** (HN's top/best union becomes internal to `HackerNewsSource`, never displayed); top-level `clout` added; **`discussions[]` is kept in a deterministic order** (oldest submit time first, then source name) so `discussions[0]` — the oldest/primary source, used as the title fallback for self-posts and listed first in the footer — is stable across runs; one `gist` per article (shared across its discussions → a cross-posted article is gisted **once**); `author` dropped (not displayed). **`discussions[].points` is load-bearing** — the interim sort reads it (see D8). Hard v1→v2 break — see Risks.

---

## Key design decisions

**Locked (from discussion):**
- **D-clout.** clout = per-source min-max of points; *no sort logic now* — plumb + persist clout; sort migration deferred.
- **D-merge.** Same canonical URL across sources → **one merged card** with every discussion link + the **max** clout. (Same-source URL dupes still merge.)
- **D-lobsters-fetch.** lobste.rs list from `https://lobste.rs/rss.rss`; points from a **separate scrape** — `hottest.json` carries `score` for (almost exactly) the same posts in one request, with `/s/{short_id}.json` as fallback.

**Decided:**
- **D1. Identity / gist-reuse key (`story_key`).** Link posts: `id = canonical_url(url)`. Self-posts: `id = "self:" + comments_url` — **namespaced** so a self-post can never collide with a link post whose article URL canonicalizes to that same `/s/` page (e.g. an HN post linking to a lobste.rs thread). If `canonical_url(url)` returns `None` for a nominal link post (degenerate/relative URL — see [models.py:124](backend/tailf/models.py#L124)), fall back to `"self:"+comments_url` and accept no cross-source merge (rare).
- **D2. clout edge cases.** `max == min` (single-post or all-equal batch) → `clout = 1.0` for all (no divide-by-zero). Missing points (HN `job` posts can lack `score`; lobste.rs always has it) → treat as batch min → `clout = 0.0`.
- **D3 (decided). `Post` carries the 4 fields PLUS `source`/`published`/`points`/`self_text`** — all free from each feed; the pipeline + interim sort need them.
- **D4 (decided). Drop `feeds` from the contract** (never displayed; superseded by `discussions[].source`). `FEED_NAMES`/`normalize_feeds` ([models.py:45,322](backend/tailf/models.py#L45)) move into `HackerNewsSource` or are removed.
- **D5. Merged-card `published` = OLDEST discussion time** (`min`) — used for BOTH the footer "x ago" and the sort. Rationale (decided): the oldest submission is closest to the source article's actual publish date, which is what the age-decay should reflect. Tradeoff accepted: a 2-day-hot HN article freshly cross-posted to lobste.rs keeps its 2-day age (it does not "reset" to fresh on the second post).
- **D6. Card footer (decided).** Stays **one line**. **Left:** `{favicon} {domain} via {Source1}, {Source2}` — favicon + domain are **plain text** (identify the article only); each **source name is a link to that source's comments page**; sources listed in `discussions[]` order (oldest first), comma-separated. **Right:** `"{x} ago"` only (the standalone "comments" word-link is **removed** — comments are reached via the source-name links). Self-posts (no `domain`): left collapses to `via {Source}` (still linking to comments). Source display labels: `{hn: "Hacker News", lobsters: "lobste.rs"}`. Keep it to one line — drop `flex-wrap`, let the domain `truncate`. Separately: fix the hardcoded end-of-feed "Hacker News" line + page `<title>` + PWA manifest description.
- **D7 (decided). `rss.rss` only** (= hottest, ~25). Skip `newest`.
- **D8. Interim sort uses `max(discussions[].points)`, NOT clout.** clout∈[0,1] is unusable in the gravity formula `(points-1)/(age+2)^1.8` — `(clout-1)` is ≤0 and inverts the order. So the interim sort keeps the existing formula fed by `Math.max(...discussions.map(d => d.points ?? 0))`; clout is persisted but unused until the deferred sort task.

---

## Phase 0 — Source abstraction (new module)

- [ ] Create `backend/tailf/sources/` (`base.py`): define `Post` (dataclass) and `FeedSource` (ABC: `name` + `get_posts()`).
- [ ] Add `min_max_clout(points: list[int|None]) -> list[float]` implementing D2 (equal/empty/None). Unit-test directly.

## Phase 1 — `HackerNewsSource` (refactor existing fetch)

- [ ] Move HN logic from [fetch.py](backend/tailf/fetch.py) into `HackerNewsSource.get_posts()`: keep `_fetch_id_list`/`_fetch_items`/`_get_json` (the topstories+beststories id-list → hydrate path, [fetch.py:49-160](backend/tailf/fetch.py#L49-L160)).
- [ ] **DELETE `fetch.union_feeds` + `fetch._merge_into`** ([fetch.py:208-256](backend/tailf/fetch.py#L208-L256)) — they're not "moved", they're **rewritten at the `Post` level**. Reimplement HN's top/best intra-source dedupe inside `HackerNewsSource`: dedupe by `hn_id` then `canonical_url`; **`points` takes the MAX** across the two feeds (not first-non-null — both lists carry a score); fill `url`/`published` gaps; there is no longer a `feeds` field to union and no `author`.
- [ ] Map each HN item → `Post`: `link = item["url"] or None`, `comments_url = f"https://news.ycombinator.com/item?id={id}"` ([fetch.py:190](backend/tailf/fetch.py#L190)), `points = score`, `published = iso_from_epoch(item["time"])`, `self_text = _strip_html(item["text"])`, `source = "hn"`. Reuses `_strip_html`, `domain_of`, `iso_from_epoch`.
- [ ] Keep the per-list 0-count WARNING ([fetch.py:107-112](backend/tailf/fetch.py#L107-L112)) for observability.

## Phase 2 — `LobstersSource` (RSS list + points scrape)

- [ ] **Declare `feedparser`** explicitly in [pyproject.toml](pyproject.toml) main deps (currently transitive-only) and add it to the [Dockerfile](backend/Dockerfile) install so the Lambda image is guaranteed to carry it. Fix the stale CLAUDE.md dep list.
- [ ] Step 1 — list: GET `https://lobste.rs/rss.rss`, parse with `feedparser`. Per entry: `title`; `link` (`entry.link`); `comments_url` (`entry.comments`, fallback `entry.id`); `short_id` — parse the `/s/{short_id}` **path segment robustly** (split on `/s/`, take the next component), not `rsplit('/')[-1]`, in case `entry.id` ever gains a trailing slug; `published` (see below); `tags = [t.term for t in entry.get("tags", [])]`.
- [ ] **Timestamp:** use `entry.published_parsed` — feedparser already returns it as a **UTC `struct_time`** (safe). Convert via `_to_iso_z`. The offset-aware caution is only about *manually* parsing the raw `pubDate` string (`...-05:00`) or routing it through `iso_from_epoch` ([models.py:76](backend/tailf/models.py#L76), expects epoch seconds) — don't do either.
- [ ] **Self-post detection:** `link`'s host is `lobste.rs` (or `"ask"`/`"show"` in tags) → `link = None`, capture `self_text` from `entry.summary` (HTML — strip tags; reuse `_strip_html`). For link posts, `entry.summary` is just a "Comments" anchor — ignore it.
- [ ] Step 2 — points: GET `https://lobste.rs/hottest.json`, build `{short_id: score}`. Join to the RSS posts by `short_id`. The two are the **same hottest ordering fetched as two non-atomic requests** — expect occasional boundary skew near the 25th item: an RSS `short_id` absent from hottest → fall back to `/s/{short_id}.json`, else `points = None` (clout 0 per D2); a hottest entry absent from RSS → ignored (the RSS list drives the batch).
- [ ] Compute `clout` over the lobste.rs batch; `source = "lobsters"`.
- [ ] Politeness: descriptive `User-Agent` + contact URL; hourly cadence fine (RSS `ttl=120`); single ~25-item page, no pagination.

## Phase 3 — Models / contract ([models.py](backend/tailf/models.py))

- [ ] Add `Discussion` dataclass: `source`, `comments_url`, `clout: float`, `points: int | None`.
- [ ] Reshape `Story` ([models.py:226-254](backend/tailf/models.py#L226-L254)): drop `hn_id`/`points`/`comments_url`/`author`/`feeds`; add `id: str`, `clout: float`, `discussions: list[Discussion]`. Keep `title`/`url`/`domain`/`published`/`image`/`gist`. Rename `hn_text` → `self_text` ([models.py:254](backend/tailf/models.py#L254)) (still `repr=False, compare=False`, not serialized).
- [ ] Update `to_dict`/`from_dict` ([models.py:259-290](backend/tailf/models.py#L259-L290)); bump `SCHEMA_VERSION = 2` ([models.py:42](backend/tailf/models.py#L42)); rewrite the contract docstring ([models.py:7-32](backend/tailf/models.py#L7-L32)).
- [ ] **Rename `GistKind` value `hn_text` → `self_text`** ([models.py:48](backend/tailf/models.py#L48)). Rationale: [StoryCard.svelte:68](frontend/src/lib/components/StoryCard.svelte#L68) renders `gist.kind.replace('_',' ')` as a badge, so a lobste.rs self-post would literally show **"hn text"**. Touchpoints to change together: [extract.py:34](backend/tailf/extract.py#L34) (`ExtractKind`), `GistKind` here, [types.ts:14](frontend/src/lib/types.ts#L14), [summarize.py:62](backend/tailf/summarize.py#L62) (`_KIND_LABEL`), and the StoryCard badge. (Aside: `_KIND_LABEL` has no `title_only` entry and `summarize` returns `None` rather than emitting `title_only` despite its docstring — pre-existing, leave or tidy.)
- [ ] Remove `FEED_NAMES`/`normalize_feeds` ([models.py:45,322](backend/tailf/models.py#L45)) from the contract (move into `HackerNewsSource` if still needed). Keep `canonical_url` ([models.py:104](backend/tailf/models.py#L104)) + `domain_of` ([models.py:152](backend/tailf/models.py#L152)) unchanged — `canonical_url` is the cross-source merge key.

## Phase 4 — Pipeline & store (cross-source merge, gist reuse, prune)

- [ ] Add `story_key(story) -> str` per **D1** (namespaced self-post keys; `None`-canonical fallback). This replaces `hn_id` as the reuse/prune/render key **everywhere**, in one atomic change (a partial swap silently mis-keys).
- [ ] Add `merge_posts(posts_by_source) -> list[Story]`, run **before** the gist phase (this is the within-run no-double-work guarantee): group link posts by `canonical_url` into a `Story` with a `Discussion` per source; self-posts (no link) stay one-per-`"self:"+comments_url` and **never merge across sources**; top-level `clout = max`, `published = min` (oldest, D5); **emit `discussions[]` sorted oldest-submit-first, then source-asc** (deterministic `discussions[0]` = oldest/primary).
- [ ] [pipeline.run](backend/tailf/pipeline.py#L43): replace `fetch_feeds`+`union_feeds` ([pipeline.py:49-50](backend/tailf/pipeline.py#L49-L50)) with `get_posts()` over the injected sources → `merge_posts`. Update the summary ([pipeline.py:65-78](backend/tailf/pipeline.py#L65-L78)): `frontpage`/`best` counts → per-source counts (`hn`/`lobsters`).
- [ ] Re-key on `story_key` (was `s.hn_id`, int → str): [store.merge](backend/tailf/store.py#L60) `existing_gists`/`existing_images` + sticky-reuse ([store.py:84-101](backend/tailf/store.py#L84-L101)) and the `new_gists`/`new_images` `dict[int,…]`→`dict[str,…]` signatures; [_stories_needing_gist](backend/tailf/pipeline.py#L194) + [_count_pruned](backend/tailf/pipeline.py#L200); [_gist_new_stories](backend/tailf/pipeline.py#L91)/[_gist_static](backend/tailf/pipeline.py#L127)/[_gist_rendered](backend/tailf/pipeline.py#L157) result maps + `by_id` ([pipeline.py:165](backend/tailf/pipeline.py#L165)) + the **unresolved filter** `s.hn_id not in gisted` ([pipeline.py:115](backend/tailf/pipeline.py#L115)); [render.render_pages](backend/tailf/render.py#L40) return type `dict[int,str]`→`dict[str,str]`, local `out` ([render.py:56](backend/tailf/render.py#L56)) and `return story.hn_id` ([render.py:66](backend/tailf/render.py#L66)). The `published`-backfill ([store.py:103](backend/tailf/store.py#L103)) stays. **Note:** gist reuse keyed on canonical URL ⇒ a cross-posted article is gisted once.
- [ ] [extract.extract](backend/tailf/extract.py#L57): self-post branch reads `story.hn_text` ([extract.py:64-66](backend/tailf/extract.py#L64-L66)) → `story.self_text`.
- [ ] [summarize.SYSTEM_PROMPT](backend/tailf/summarize.py#L36) + `_KIND_LABEL` ([summarize.py:62](backend/tailf/summarize.py#L62)): soften "Hacker News reader"/"Hacker News self-post" to source-neutral. Prompt stays cached.

## Phase 5 — Config & DI

- [ ] [config.py](backend/tailf/config.py): add `lobsters_rss_url` (`https://lobste.rs/rss.rss`), `lobsters_hottest_url` (`https://lobste.rs/hottest.json`), `lobsters_enabled` (`TAILF_LOBSTERS_ENABLED`, default `true`), lobste.rs `User-Agent`. Mirror `from_env` ([config.py:142-165](backend/tailf/config.py#L142-L165)). Move `id_list_sources` ([config.py:167-178](backend/tailf/config.py#L167-L178)) into `HackerNewsSource`.
- [ ] [di.py](backend/tailf/di.py): `injector` has **no bare `list[T]` multibinding**. Add a small wrapper — `@dataclass class SourceRegistry: sources: list[FeedSource]` — and a `@singleton @provider def provide_sources(self, config) -> SourceRegistry` that builds `[HackerNewsSource(config), *([LobstersSource(config)] if config.lobsters_enabled else [])]`. `pipeline.run` resolves `injector.get(SourceRegistry).sources` (today it calls `fetch_feeds(config)` directly and takes no sources arg — change that).

## Phase 6 — Frontend

- [ ] [types.ts](frontend/src/lib/types.ts): add `Discussion` + `Source`; reshape `Story` ([types.ts:26-47](frontend/src/lib/types.ts#L26-L47)) — `hn_id:number`→`id:string`, move `points`/`comments_url` into `discussions[]`, add `clout`, drop `feeds`/`author`; drop `Feed` ([types.ts:11](frontend/src/lib/types.ts#L11)); rename `GistKind` `hn_text`→`self_text` ([types.ts:14](frontend/src/lib/types.ts#L14)).
- [ ] [data.ts](frontend/src/lib/data.ts): `isStory` ([data.ts:22-31](frontend/src/lib/data.ts#L22-L31)) — replace `typeof s.hn_id==='number'`+`Array.isArray(s.feeds)` with `typeof s.id==='string'`+`Array.isArray(s.discussions)`. **AND** harden `isDataFile`/`loadData` to require `schema_version === 2` and throw `DataError` on mismatch — see the offline-transient risk below; today a cached v1 file passes `isDataFile` ([data.ts:33-37](frontend/src/lib/data.ts#L33-L37)) and then `isStory` silently drops every story → empty feed, no error, no auto-refetch offline.
- [ ] [hotness.ts](frontend/src/lib/hotness.ts) (per **D8** — sort migration deferred, but it must compile + keep working): `story.points` reads ([hotness.ts:28,52](frontend/src/lib/hotness.ts#L28)) → `Math.max(...story.discussions.map(d => d.points ?? 0))`; tiebreaker `a.hn_id - b.hn_id` ([hotness.ts:59](frontend/src/lib/hotness.ts#L59)) → `a.id.localeCompare(b.id)`. Do **not** sort by clout yet.
- [ ] [StoryCard.svelte](frontend/src/lib/components/StoryCard.svelte) — implement the D6 footer. Title `primaryHref` ([L9](frontend/src/lib/components/StoryCard.svelte#L9)) = `url ?? discussions[0].comments_url` (oldest/primary; stable via the deterministic order). Footer ([L74-108](frontend/src/lib/components/StoryCard.svelte#L74-L108)), one line: **left** = favicon + `domain` (both plain text, not links) + `via ` + the source names from `discussions[]`, comma-separated, **each a link to `discussion.comments_url`** (label via `{hn:"Hacker News", lobsters:"lobste.rs"}`); **right** = the `<time>` only ([L96-98](frontend/src/lib/components/StoryCard.svelte#L96-L98)) — **delete the standalone "comments" `<a>`** ([L99-106](frontend/src/lib/components/StoryCard.svelte#L99-L106)). Self-post (`domain` null): left = `via {Source}`. Drop `flex-wrap` ([L76](frontend/src/lib/components/StoryCard.svelte#L76)) so it stays one line; `domain` keeps `truncate`. Favicon ([L16-18](frontend/src/lib/components/StoryCard.svelte#L16-L18)) stays article-domain-based. Badge ([L68](frontend/src/lib/components/StoryCard.svelte#L68)) shows `self text` after the kind rename.
- [ ] [+page.svelte](frontend/src/routes/+page.svelte): `{#each visible as story (story.hn_id)}` ([L136](frontend/src/routes/+page.svelte#L136)) → `(story.id)`; fix the hardcoded "Hacker News" end-of-feed line ([L143-150](frontend/src/routes/+page.svelte#L143-L150)) → dynamic from sources present (or "Hacker News + lobste.rs"); update `<title>` ([L99](frontend/src/routes/+page.svelte#L99)). `s.gist` filter ([L33](frontend/src/routes/+page.svelte#L33)) unchanged.
- [ ] [vite.config.ts](frontend/vite.config.ts): update the PWA manifest `description` ("Top Hacker News stories") to reflect both sources. **Bump the Workbox runtime cache name** for `/data.json` ([vite.config.ts:108-119](frontend/vite.config.ts#L108-L119)) so the v2 deploy doesn't serve a stale v1 body from the offline NetworkFirst cache.
- [ ] Update the **single** sample [.data/data.json](.data/data.json) to v2 with a lobste.rs discussion and one merged (2-discussion) card so `just serve` works pre-`just index`. **Do NOT create `frontend/static/data.json`** (would deploy to S3 and clobber the Lambda's file).

## Phase 7 — Tests ([backend/tests/](backend/tests/))

- [ ] Add a shared `conftest.py` `make_story()`/`make_post()` factory (v2 shape: `id`/`clout`/`discussions`) — kills the per-file fixture duplication in [test_store.py:36-49](backend/tests/test_store.py#L36-L49), [test_pipeline.py:15-28](backend/tests/test_pipeline.py#L15-L28), [test_models.py:16-35](backend/tests/test_models.py#L16-L35), [test_fetch.py](backend/tests/test_fetch.py).
- [ ] New `test_sources.py`: `min_max_clout` edges (D2); `LobstersSource` via canned `rss.rss` + `hottest.json` — robust `short_id` parse, self-post detection (lobste.rs-host link, `ask` tag), UTC timestamp, points join + boundary-skew fallback, clout.
- [ ] Refactor [test_fetch.py](backend/tests/test_fetch.py) → `HackerNewsSource` tests (its MockTransport HN fixtures; the old `test_union_*` become intra-source dedupe tests — assert **max points wins**).
- [ ] **Cross-source merge test (critical):** HN + lobste.rs posts with the same canonical URL → **one** `Story`, **two** `discussions` in deterministic order, top-level `clout = max`, `published = max`. Plus: a self-post never merges across sources; a link post whose URL is `None`-canonical keys on `self:` and stays separate; two different URLs stay separate.
- [ ] Update [test_store.py](backend/tests/test_store.py) (gist/image reuse by `story_key`; a cross-posted article keeps one gist), [test_pipeline.py](backend/tests/test_pipeline.py) (`_stories_needing_gist`/`_count_pruned` on string keys), [test_models.py](backend/tests/test_models.py) (v2 round-trip, `schema_version==2`, asserted key set drops `hn_id`/`feeds`/`points`/`author`/`comments_url`), [test_render.py](backend/tests/test_render.py) (string-keyed `render_pages`). Remove the `normalize_feeds`/`feeds` tests ([test_models.py:106-114](backend/tests/test_models.py#L106-L114)).

## Phase 8 — Docs

- [ ] [CLAUDE.md](CLAUDE.md): rewrite "What this is" + Architecture for multi-source; document `FeedSource`/`Post`/`clout` (recomputed each run; comparability caveat), the merged-card rule + deterministic `discussions` order, `canonical-url → one gist` reuse, schema v2, lobste.rs RSS-list + points-scrape. **Fix the stale claims:** feedparser is a real dep now; there is no `frontend/static/data.json` fallback (correct the dev-middleware description). Update the backend module map.
- [ ] [README.md](README.md): "aggregates Hacker News" → "Hacker News + lobste.rs". [.env.example](.env.example): add `TAILF_LOBSTERS_*`.
- [ ] Keep the contract in lockstep: [models.py](backend/tailf/models.py) `to_dict` ↔ [types.ts](frontend/src/lib/types.ts) ↔ the one sample [.data/data.json](.data/data.json).

---

## Deferred (out of scope here)

- **The final feed sort.** clout (timeless — score-only, no age term) is plumbed + persisted now. The deferred task designs how clout (the popularity input) combines with the oldest `published` (the recency input) to order the merged feed — and resolves the clout batch-size/comparability caveat. On a cross-posted link the sort inputs are already chosen: `clout = max`, `published = min` (oldest). Until that lands, the **interim** sort uses the existing gravity formula on `max(discussions.points)` + age, NOT clout (D8).
- lobste.rs **tags** as displayed chips; lobste.rs **`newest`/per-tag** feeds (D7 — hottest-only for v1).

## Operational notes

- **Ship it.** lobste.rs gisting is in; `TAILF_LOBSTERS_ENABLED` defaults **on**. Still send a descriptive `User-Agent` + contact URL and keep the hourly cadence — practical politeness that avoids rate-limiting/blocking, regardless of any content directives.
- **Payload/cost:** +~25 posts grows `data.json` + per-run gist count; the cross-post merge (one card, one gist) softens it. Watch, don't gate.

## Risks / contract-break checklist (v1 → v2 is a hard break)

- **Atomic multi-file change.** `to_dict` ([models.py:259](backend/tailf/models.py#L259)) + `types.ts` Story + `isStory` ([data.ts:22](frontend/src/lib/data.ts#L22)) + the sample [.data/data.json](.data/data.json) must land together, or `isStory` drops every story (logs a warning, shows an empty feed).
- **v1→v2 OFFLINE transient (silent empty feed).** The SW caches `/data.json` NetworkFirst (24h, 5s network timeout, [vite.config.ts:108-119](frontend/vite.config.ts#L108-L119)). Post-deploy, an offline/slow PWA is served cached **v1**; `isDataFile` still passes (schema_version is a number), `isStory` drops all stories → "No stories…" with no error and no refetch. Mitigation: the Phase 6 `schema_version === 2` hard check + bumped cache name.
- **`hn_id` is referenced in every tier — one atomic swap.** Grep `hn_id` exhaustively: [fetch.py](backend/tailf/fetch.py), [store.py:84-101](backend/tailf/store.py#L84-L101), [pipeline.py:115,122-123,140,154,165,194,200](backend/tailf/pipeline.py#L115), [render.py:56,66](backend/tailf/render.py#L56), [hotness.ts:59](frontend/src/lib/hotness.ts#L59), [+page.svelte:136](frontend/src/routes/+page.svelte#L136), [types.ts:28](frontend/src/lib/types.ts#L28), [data.ts:26](frontend/src/lib/data.ts#L26), every fixture. A missed int/str key mismatch only fails when a story flows through that path (e.g. render fallback → `KeyError` or a redundant re-gist).
- **Timestamp parsing.** Routing lobste.rs's offset-aware `pubDate` through `iso_from_epoch` ([models.py:76](backend/tailf/models.py#L76)) → null `published` → backfilled to `generated_at` ([store.py:103](backend/tailf/store.py#L103)) → every lobste.rs post looks brand-new. Use `feedparser.published_parsed` (already UTC).
- **Self-post URL + key collision.** HN uses `url == None`; lobste.rs RSS self-posts have `link` at `lobste.rs` — detect by host. Namespace self-post `story_key` (`self:`) so it can't equal a link post's canonical_url (D1).
- **Non-deterministic `discussions[]` order.** Without the oldest-first/source-asc sort, merge arrival order flips between runs — the self-post `primaryHref` jumps sources and the footer's comma-separated source links reorder. The deterministic sort in `merge_posts` is required, not cosmetic.
