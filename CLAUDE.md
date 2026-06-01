# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: implemented & locally verified; not yet deployed

All three subsystems build and pass locally: `poetry install` + `just test` → **63 passed**; `just lint` → ruff + `svelte-check` clean; `just build` → static site + PWA service worker; the CDK stack **synthesizes** to 21 resources (verified against `aws-cdk-lib` 2.257). `poetry.lock` and `frontend/pnpm-lock.yaml` are committed. What remains: a **live pipeline run** (`just index` — needs `ANTHROPIC_API_KEY` + network; never executed against the real feeds/LLM) and an actual **`cdk deploy`** (needs AWS creds, Docker, and the `hackergist/anthropic-api-key` secret created out of band).

### Environment gotchas (already handled, but worth knowing)
- `.python-version` pins **3.14.3** (the installed patch). Poetry can pick a wrong interpreter because `python` on PATH may be an older pyenv version — `just setup` runs `poetry env use "$(pyenv which python3.14)"` first to force 3.14.
- The frontend uses **pnpm via corepack** (`corepack enable pnpm`). pnpm 10+ blocks native build scripts; `frontend/pnpm-workspace.yaml` allow-lists `esbuild` + `sharp`.
- `cdk` is invoked from the **repo root** (per the root `cdk.json`), so the stack resolves the Docker/asset directories as **absolute** paths anchored to the stack file — not `../backend` relative to cwd.

**This CLAUDE.md is the source of truth** for *intent* — the resolved architecture, locked decisions, edge cases, and open tuning items (gist prompt wording, refresh cadence, hotness formula). Read the relevant section before changing behavior.

**Keep this CLAUDE.md and [README.md](README.md) current as the repo evolves** — correct any decision that changes and prune anything that no longer holds. A stale doc is worse than none.

## What this is

hackergist.dev — a static, installable PWA that aggregates the top stories from **Hacker News + lobste.rs**, each with a 1–2 sentence AI "gist" of the **linked article** (not the discussion thread). Titles link to the source; the card footer links to each community's discussion. An article posted to both sources is one merged card with both discussion links.

## Architecture (big picture)

A scheduled batch pipeline writes one JSON file; a static frontend renders it. There is no application server.

```
EventBridge (hourly, top of hour) → Lambda (Docker, Python 3.14)
    each FeedSource → Posts:
      HackerNewsSource: topstories+beststories (Firebase API) → hydrate → Posts
      LobstersSource:   rss.rss (post list) + hottest.json (scores) → Posts
    → merge Posts across sources by canonical URL → Story records (one article,
      one-or-more discussions) keyed by story.id → diff vs current data.json
    → for NEW stories only: fetch article → extract text → Claude Haiku gist
    → reuse existing gists/images by story.id → write merged data.json (FileSystem → S3)
CloudFront → serves the static SvelteKit PWA + data.json (same-origin)
Browser → fetches /data.json on load; service worker caches it for offline/installed use
```

Three top-level pieces in one monorepo:
- `backend/` — Python pipeline (package `hackergist`) + `Dockerfile` + `tests/`.
- `frontend/` — SvelteKit 5 static PWA (Tailwind v4, `@vite-pwa/sveltekit`).
- `infra/` — AWS CDK (Python): `app.py` + `hackergist_stack.py`. `cdk.json` lives at the **repo root** (`app: "python infra/app.py"`); there is intentionally only one.

### Backend module map (`backend/hackergist/`)
`config.py` (all tunables from `HACKERGIST_*` env, with defaults) · `models.py` (`Story`, `Discussion`, `Gist`, `DataFile` + `to_dict`/`from_dict`, `canonical_url`, `domain_of`, `discussion_key`) · `sources/` (the `FeedSource` ABC + `Post` + `min_max_clout`/`assign_clout` + `SourceRegistry` in `base.py`; `HackerNewsSource` in `hackernews.py`; `LobstersSource` in `lobsters.py`) · `filesystem.py` (`FileSystem` ABC, `LocalFileSystem`, `S3FileSystem`) · `di.py` (`injector` module + `build_injector`; provides the enabled `SourceRegistry`) · `extract.py` (static article text + fallbacks; `extract_article_from_html` is shared with the render path) · `render.py` (headless-Chromium fallback for JS/SPA pages) · `summarize.py` (Anthropic gist) · `store.py` (read/merge/prune/write, keyed by `story.id`) · `pipeline.py` (`merge_posts` cross-source merge → two-phase gist: static → render fallback) · `handler.py` (Lambda entry) · `cli.py` (`python -m hackergist.cli`, used by `just index`).

## Non-obvious decisions (easy to get wrong — honor these)

- **One `data.json` (schema_version 2) is the entire data layer.** It holds the *union of all sources* — `HackerNewsSource` (topstories+beststories, capped via `config.top_limit`/`best_limit`) and `LobstersSource` (rss.rss). No database. Frontend fetches it same-origin. Each record is **one article (or self-post) with a `discussions[]` array** — one `Discussion` (`source`/`comments_url`/`clout`/`points`) per community that posted it; the same link on both sources is **one merged card**. The record key `story.id` is the canonical article URL (link posts) or `self:<comments_url>` (self-posts) — see `models.discussion_key`. Stories absent from **every** source this run are pruned. The exact shape is the contract — keep `backend/hackergist/models.py` `to_dict`, `frontend/src/lib/types.ts`, and the sample `frontend/data.sample.json` in lockstep.
- **Gists (and images) are reused by `story.id`; only new stories are summarized.** Never re-summarize a story still present — the cost control (every new story gisted each run; no per-run cap). Because `id` is the canonical URL, a **cross-posted article is scraped + gisted once**, and the cross-source merge happens BEFORE the gist phase (`pipeline.merge_posts`) so within a run it's never double-scraped either.
- **`gist.kind`** ∈ `article | self_text | readme | pdf | title_only` (`self_text` = any self/text post — HN Ask/Show, lobste.rs `ask`). `gist` is `null` **only** when gisting genuinely failed (an API error) — the frontend then shows title-only.
- **`clout` is a per-source 0..1 min-max of `points`** (1.0 = the top-pointed post that source returned, 0.0 = the lowest), assigned by each `FeedSource` (`min_max_clout`). It is *timeless* — strictly a function of points, no age term — and recomputed every run (NOT sticky like the gist). A merged record carries the **max** clout across discussions. clout is the intended input to the eventual cross-source sort.
- **Hotness sort is computed client-side at render** (interim), not stored. `frontend/src/lib/hotness.ts` uses `(points-1)/(age_hours+2)^1.8` where `points` = max across `discussions[]` and the age term uses the **oldest** discussion's `published` (closest to the article's publish date); a `now` clock ticks every 60s so it decays live. clout is **not** used here (0..1 would invert the gravity formula) — the clout-based cross-source sort is a deferred task. `config.hotness_gravity` is documentation-only.
- **The UI shows only stories WITH a gist** (`+page.svelte` filters `gist != null`); gist-less ones (unreadable sources) are hidden but the backend keeps retrying them each run. Cards are title → image → gist → footer. The `image` field (`og:image`/`twitter:image`, captured during extraction, reused by `story.id` like the gist) renders when present, else the card is image-less. Footer is **one line**: left = favicon + `domain` (plain text) + `via ` + each discussion's **source name as a link to that source's comments** (`Hacker News` / `lobste.rs`, listed oldest-first, comma-separated); right (`ml-auto`) = the time only. There is **no standalone "comments" link** (the source names are the discussion links) and **no points/author** displayed. The favicon comes client-side from Google's favicon service keyed on `domain`. The "N posts from {sources present}, summarized by AI · updated X ago" line sits at the **bottom** of the list. The site `<footer>` ([+layout.svelte](frontend/src/routes/+layout.svelte)) carries a Ko-fi "Buy me a coffee" button and `© {year} · made by Aaron Mamparo`.
- **Feed freshness in the installed PWA.** `data.json` is cached **NetworkFirst** (fresh online, cached offline), but the SPA only fetched it once per page load — so a reopened/backgrounded standalone PWA showed stale data. `+page.svelte` now does a *soft* refetch (keeps the current cards on screen; no skeleton; on failure keeps what's shown) on `visibilitychange`/`pageshow` when the data is older than `STALE_AFTER_MS` (60s). `PullToRefresh.svelte` adds a touch pull-to-refresh (window-level touch listeners since the body is the scroll container; non-passive `touchmove` to `preventDefault` native overscroll only once a downward pull at scrollTop 0 is committed) with an iOS-style **rubber-band** drag (`rubberBand()`: asymptotic toward `PULL_LIMIT`, never a hard clamp). `app.css` sets `overscroll-behavior-y: contain` so the browser's own pull-to-refresh doesn't fight it, makes `<img>` non-selectable/undraggable + `-webkit-tap-highlight-color: transparent` (so a thumb brushing a thumbnail mid-scroll can't leave a stuck green `::selection` tint). `ScrollToTop.svelte` shows a circular "Back to top" FAB in the **bottom-right** (safe-area-inset aware) after scrolling past ~500px; it does a fast distance-independent `requestAnimationFrame` scroll (respecting `prefers-reduced-motion`) then soft-reloads — rendered *outside* `PullToRefresh` so the pull `transform` doesn't re-anchor its `position: fixed`. Loads are coalesced via an `inFlight` guard so the initial/foreground/pull/back-to-top paths can't overlap. **A failed fetch (≥4xx, network drop, bad JSON) never replaces cards already on screen** — `load`'s catch only surfaces the error state when `data` is still null (nothing to show yet). **Schema migration:** `data.ts` `isDataFile` requires `schema_version === 2`, so a stale cached v1 body (served offline by NetworkFirst) is rejected with a `DataError` rather than silently rendering an empty feed; the Workbox runtime cache for `/data.json` was renamed `hackergist-data-v2` so the v2 deploy drops the old cached body.
- **LLM = Claude Haiku via the direct Anthropic API** (`anthropic` SDK), model id `claude-haiku-4-5` — **not Bedrock**. The SDK reads `ANTHROPIC_API_KEY` from the env. **Locally**, `cli.py` calls `load_dotenv(find_dotenv(usecwd=True))` so a gitignored `.env` at the repo root supplies `ANTHROPIC_API_KEY` (and any `HACKERGIST_*` overrides) for `just index`; see `.env.example`. **In Lambda**, the CDK **creates** the Secrets Manager secret (`hackergist/anthropic-api-key`), **seeding it from `ANTHROPIC_API_KEY` in `.env`** at synth time (`app.py` loads `.env`), and injects it into the function as a `{{resolve:secretsmanager}}` dynamic reference. Tradeoff: the key lands as plaintext in the synthesized template (`cdk.out`, gitignored) — accepted for this solo project. `handler.py` never imports `cli.py`, so dotenv isn't used at runtime there. `summarize.py` caches the system prompt and degrades to `None` on API errors.
- **Sources are `FeedSource`s; the pipeline is source-agnostic.** Each implements `get_posts() -> list[Post]` and assigns `clout` over its own batch. `HackerNewsSource` does the Firebase id-list → hydrate flow (intra-source dedupe by canonical URL, max points wins). `LobstersSource` reads `rss.rss` for the post list (via `feedparser`) and scrapes `hottest.json` for `score` (joined by `short_id`, with a per-story `/s/{id}.json` fallback for boundary skew); self/text posts are detected by an own-thread link or an `ask`/`show` tag. `di.py` provides the enabled `SourceRegistry` (HN always; lobste.rs when `HACKERGIST_LOBSTERS_ENABLED`, **default on**); lobste.rs requests use `config.lobsters_user_agent`. `pipeline.merge_posts` unions Posts across sources by canonical URL into `Story` records.
- **All persistence goes through a `FileSystem` abstraction wired with `injector` DI.** `di.py` selects `S3FileSystem` when **`AWS_LAMBDA_FUNCTION_NAME`** is present (bucket from `HACKERGIST_BUCKET`, which it requires), else `LocalFileSystem` rooted at `.data/`. `store.py`/`pipeline.py` talk **only to the injected interface** — never to disk or `boto3`/S3 directly.
- **CDK looks up the existing Route 53 zone** with `HostedZone.from_lookup(domain_name="hackergist.dev")` — do **not** create a hosted zone. Apex-only: alias **A + AAAA** records to the CloudFront distribution (no `www`).
- **ACM cert is in us-east-1** (CloudFront requirement); the whole stack runs in us-east-1. The stack `env` must be a **concrete account + region** for `from_lookup` to resolve — it caches into `cdk.context.json`, which is **git-tracked** (not ignored).
- **Lambda is a Docker image**, `FROM public.ecr.aws/lambda/python:3.14` (AL2023 base → `dnf`, not `yum`), built from the `backend/` context; `CMD ["hackergist.handler.handler"]`.
- **CloudFront serves the SPA**: `adapter-static` emits `fallback: index.html`; the distribution maps 403/404 → `/index.html`. `data.json` has its own short-TTL behavior. `BucketDeployment` uses `prune: false` so deploying the frontend doesn't wipe the Lambda-written `data.json`.

## Toolchain & commands

**pnpm** (Node via nvm, `.nvmrc` → `lts/*`) for the frontend; **poetry** (Python via pyenv, `.python-version` → 3.14.x) for backend + infra. The `cdk` CLI is the separate `aws-cdk` npm package (installed by `just setup`), distinct from the `aws-cdk-lib` construct library in the `infra` poetry group. The root **`justfile`** is the dev interface:

| Recipe        | What it runs |
|---------------|--------------|
| `just setup`  | `poetry install`; `cd frontend && pnpm install`; `npm i -g aws-cdk` |
| `just index`  | `poetry run python -m hackergist.cli` → writes `.data/data.json` |
| `just serve`  | `cd frontend && pnpm dev` (dev middleware serves `.data/data.json`, falling back to the committed `frontend/data.sample.json`, at `/data.json`) |
| `just build`  | `cd frontend && pnpm build` → `frontend/build/` |
| `just synth`  | depends on `build`; `poetry run cdk synth` |
| `just deploy` | depends on `build`; `poetry run cdk deploy --all` |
| `just lint`   | `poetry run ruff check .` + `cd frontend && pnpm run check` |
| `just fmt`    | `poetry run ruff format .` |
| `just test`   | `poetry run pytest` |
| `just icons`  | `cd frontend && pnpm run gen-icons` (re-rasterize PNGs from the SVG sources via sharp) |

`.data/` is a **gitignored local stand-in for the S3 bucket** (`.data/*` is ignored except the tracked `.data/.gitkeep`). The committed dev sample is **`frontend/data.sample.json`** (the `just serve` middleware falls back to it before the first `just index`); it must NOT live under `frontend/static/` — anything there is copied into `build/` and would be deployed to S3, clobbering the Lambda-written `data.json`.

`pyproject.toml` is a single poetry project: package `hackergist` from `backend/`, groups **main** (`httpx`, `feedparser`, `trafilatura`, `pypdf`, `boto3`, `injector`, `anthropic`, `python-dotenv`, `tqdm`, `playwright`), **infra** (`aws-cdk-lib`, `constructs`), **dev** (`pytest`, `ruff`). `playwright` also needs its browser: `just setup` runs `playwright install chromium`, and the Dockerfile installs Chromium + AL2023 libs (finicky in Lambda — see the Dockerfile; disable with `HACKERGIST_RENDER_ENABLED=false`).

## Edge cases the pipeline handles

Non-HTML links (PDF via `pypdf` → `kind=pdf`; GitHub repo → raw README → `kind=readme`), self/text posts with no external URL (`url=null`, gist the captured `self_text` → `kind=self_text`: HN Ask/Show, lobste.rs `ask`), very long articles (truncated to `HACKERGIST_ARTICLE_CHAR_BUDGET`). **JS/SPA pages**: static extraction rejects "enable JavaScript" shells / too-thin output, then the render fallback retries with headless Chromium — that recovers most SPAs (~half of the no-gist set in practice). **No useful gist** (`gist=null`, story hidden) when a page is genuinely unreadable (hard paywall, login wall, dead link), when the model replies `SKIP` to junk/boilerplate, or on an LLM error. We **never** gist the title alone. **lobste.rs has no score in RSS** — `LobstersSource` joins `hottest.json` by `short_id` (per-story `/s/{id}.json` fallback) and its `created_at` is offset-aware (parsed via `feedparser.published_parsed`, already UTC — never via `iso_from_epoch`). **Dedupe** is by canonical URL (strip tracking params, normalize http/https + trailing slash): within a source the max-points post wins; across sources the same URL becomes one merged card with both discussions, while self-posts (no link) are keyed per-source and never merge.
