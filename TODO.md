# TODO — hackergist

*hackergist.dev — a public, installable site that aggregates top Hacker News posts, each with a 1–2 sentence "gist." Titles link straight to the source URL; a secondary link goes to the HN discussion.*

## Problem
Skimming Hacker News means opening a dozen tabs just to learn what each link *is*. hackergist shows the day's top HN stories, each with a one- or two-sentence gist of the **linked content**, so you can decide what's worth a click without the round trip.

## Sources & data universe
- Frontpage — https://hnrss.org/frontpage  ·  Best — https://hnrss.org/best
- The persisted/displayed universe = the **union of the parameterless responses from both feeds** (≈50–60 unique stories). Small enough that all frontend data lives in a **single `data.json` in S3**.
- hnrss item `<link>` = the article URL; the HN comments URL is provided separately in the item.

## Architecture (resolved)
EventBridge (~every 30 min) → **Lambda (Docker, Python 3.14)**: fetch both feeds → union + dedupe by HN id → diff vs the current `data.json` → for *new* stories: fetch the article → extract main text → summarize to ≤2 sentences (**Claude Haiku**); reuse existing gists for stories already present → write the merged **`data.json`** (via the FileSystem abstraction → S3) → **CloudFront** serves the static **SvelteKit PWA** + `data.json` (same-origin) → installable on phone.

- `data.json` holds the current union, with gists. Stories that drop out of *both* feeds are pruned; gists are reused by HN id, so nothing is re-summarized while a story is still present. (If re-entry churn ever costs meaningfully, add a small gist cache — not needed initially.)
- The frontend displays the **entire** current union, ordered by a **bespoke hotness score** = f(newness, HN points), computed at render from each story's stored `points` + `published` (so recency keeps decaying between 30-min refreshes).
- The SPA fetches `/data.json` on load and the service worker caches it for offline / installed use.

## Stack (resolved)
**Frontend** — SvelteKit (Svelte 5) + `adapter-static` (fully prerendered, no server); Tailwind CSS v4 (`@tailwindcss/vite`); PWA via `@vite-pwa/sveltekit` (manifest + service worker → installable). Clean, minimal, fully responsive, dark mode. Node via **nvm** (`.nvmrc` → `lts/*`), package manager **pnpm** (fast, strict, first-class with Vite/SvelteKit; npm = fallback).

**Backend / pipeline** — Python **3.14** (newest AWS Lambda-supported; AWS labels it the latest LTS), managed with **pyenv** + **poetry**. Packaged as a **Docker image Lambda** from `public.ecr.aws/lambda/python:3.14` (AL2023 → use `dnf`, not `yum`). Initial libs: `httpx`, `feedparser`, `trafilatura` (extraction), `boto3`, `injector` (DI), `anthropic` (gists via **Claude Haiku**, direct **Anthropic API** — not Bedrock).

**Infra** — **AWS CDK (Python)**, matching the backend toolchain: S3 (static site + `data.json`), CloudFront + DNS-validated ACM cert, the **existing Route 53** hosted zone for hackergist.dev (looked up, not created) with alias records, the Docker Lambda (+ ECR), EventBridge schedule.

**Repo** — single monorepo; **justfile** at root for dev QoL.

## Local development
- A gitignored **`.data/`** directory stands in for the S3 bucket; `data.json` (and any cached artifacts) live there during local dev.
- Python uses a **`FileSystem` abstraction** wired with the **`injector`** DI module: `LocalFileSystem` (root `.data/`) when running locally, `S3FileSystem` (the bucket) when running in Lambda — selected by env (detect `AWS_LAMBDA_FUNCTION_NAME`, else an explicit `HACKERGIST_ENV`). The pipeline / `store.py` only ever talk to the injected `FileSystem`, never to disk or S3 directly.
- **`just index`** — run the pipeline locally (poetry) → writes `.data/data.json`.
- **`just serve`** — run the SvelteKit dev server; in dev it reads the local `data.json` from `.data` (a Vite dev middleware serves `.data/data.json` at `/data.json`, mirroring the same-origin prod fetch).

## Repo layout (proposed)
```
hackergist/
├── justfile                # recipes: setup / index / serve / build / deploy / lint / test
├── .python-version         # pyenv → 3.14.x
├── .nvmrc                  # nvm  → lts/*
├── pyproject.toml          # poetry; groups: main (runtime), infra (cdk), dev (pytest/ruff)
├── poetry.lock
├── .data/                  # gitignored — local stand-in for the S3 bucket (holds data.json)
├── README.md  ·  TODO.md
├── backend/
│   ├── hackergist/         # pipeline package
│   │   ├── fetch.py        # pull both hnrss feeds, parse, union, dedupe
│   │   ├── extract.py      # article main-text extraction (+ fallbacks)
│   │   ├── summarize.py    # Claude Haiku gist (≤2 sentences)
│   │   ├── filesystem.py   # FileSystem abstraction + LocalFileSystem(.data) / S3FileSystem(bucket)
│   │   ├── di.py           # injector Module: bind FileSystem by env (local vs AWS)
│   │   ├── store.py        # read / merge / write data.json via the injected FileSystem
│   │   ├── models.py       # Story / Gist
│   │   ├── handler.py      # Lambda entrypoint (build Injector → run pipeline)
│   │   └── cli.py          # local entrypoint for `just index`
│   ├── Dockerfile          # FROM public.ecr.aws/lambda/python:3.14
│   └── tests/
├── infra/                  # CDK (Python): S3, CloudFront, ACM, Route 53 (lookup), Lambda(Docker), EventBridge, ECR
│   └── app.py
└── frontend/               # SvelteKit static PWA
    ├── package.json  ·  svelte.config.js (adapter-static)  ·  vite.config.ts (pwa + tailwind + dev /data.json)
    ├── src/{routes, lib, app.html, app.css}    # incl. lib/hotness.ts (render-time sort)
    └── static/{manifest.webmanifest, icons/, …}
```

## Open / to tune (no blockers — all resolve during build)
1. **Gist prompt** — exact wording + per-article token budget (tune once we see real output).
2. **Refresh cadence** — defaulting to 30 min; tune freely.
3. **Hotness formula** — the exact f(newness, points). Starting point: HN's own ranking, ~`(points − 1) / (age_hours + 2)^1.8`; tune gravity / weighting once we see real ordering.

## Milestones

> **Status (2026-05-30):** M0–M5 are **built and locally verified green.** Toolchain installs (`poetry`/`pnpm`); `just test` → **40 passed**, `just lint` → ruff + `svelte-check` clean, `just build` → static site + PWA service worker, and the CDK stack **synthesizes** to 21 resources (against `aws-cdk-lib` 2.257). `poetry.lock` + `pnpm-lock.yaml` committed. **Still not done:** a **live pipeline run** (`just index` — needs `ANTHROPIC_API_KEY` + network; never run against real feeds/LLM) and an actual **`cdk deploy`** (needs AWS creds, Docker, and the `hackergist/anthropic-api-key` secret created out of band).

### M0 — Scaffolding
- [x] Create repo
- [x] Monorepo skeleton: justfile, pyenv/poetry, nvm/pnpm, SvelteKit + Tailwind + PWA, CDK app, Dockerfile
- [x] justfile recipes: `setup`, `index`, `serve`, `build`, `deploy`, `lint`, `test` (+ `synth`, `fmt`, `icons`)
- [x] `.data/` local S3 stand-in (gitignored); `FileSystem` abstraction + `injector` bindings (LocalFileSystem ↔ S3FileSystem, chosen by `AWS_LAMBDA_FUNCTION_NAME`)
- [x] Config: feed URLs, refresh interval, model name, hotness param, S3 bucket, etc. — all `HACKERGIST_*` env with defaults (`config.py`)

### M1 — Ingest (Python)
- [x] Fetch both hnrss feeds (concurrent); timeouts, retries *(conditional GET on the feeds deferred — see M3)*
- [x] Parse → `{hn_id, title, url, comments_url, points, author, published}`; union + dedupe by HN id (+ canonical URL)

### M2 — Gist pipeline (the meat)
- [x] Fetch linked page → extract main text → truncate to char budget
- [x] Summarize ≤2 sentences via Claude Haiku (neutral, technical); store gist + model + timestamp + `kind`
- [x] Fallbacks: GitHub → README, PDF → text (`pypdf`), video/paywall/dead → title-only; Ask/Show/text → gist the HN text
- [x] Reuse gists by HN id; only summarize new stories (capped per run)

### M3 — Persist
- [x] Merge into a single `data.json`; write via the injected FileSystem (S3 in AWS, `.data/` locally); prune stories no longer in either feed
- [ ] Keep fetch metadata (etag / last-seen) for politeness *(not implemented — conditional GET deferred)*

### M4 — Frontend (SvelteKit PWA)
- [x] Fetch `/data.json`; **display the entire current union**; list view: **title → source**, gist, meta (domain, points, HN-comments link, time)
- [x] **Hotness sort** — bespoke score `f(newness, points)` computed client-side at render; a `now` clock ticks every 60s so recency decays live between refreshes (`lib/hotness.ts`)
- [x] Source filter (All / Frontpage / Best) + per-story source badge *(per-domain favicon: optional, not done)*
- [x] Responsive + minimal + dark mode; service worker (installable; NetworkFirst cache of `data.json`)
- [x] **App icon — Claude-designed (mint terminal-chevron `›` + baseline underscore on a dark tile).** SVG sources + the PWA PNG set (192/512/512-maskable `purpose: "any maskable"`/apple-touch-180/favicon-32) + `gen-icons.mjs`. *Caveat: the larger PNGs were rasterized from an earlier SVG (chevron only, missing the baseline) — run `just icons` once `sharp` is installed to regenerate the full set.*

### M5 — Deploy (CDK) — *authored & synthesizes; NOT yet deployed*
- [x] S3 (private, OAC) + CloudFront; HTTPS, SPA 403/404 → `/index.html`; `data.json` short-TTL behavior
- [x] Use the **existing** Route 53 hosted zone — `HostedZone.from_lookup(...)` (does **not** create a zone); apex alias **A + AAAA** → CloudFront
- [x] DNS-validated **ACM cert in us-east-1** (stack runs in us-east-1 so the cert is colocated)
- [x] Stack `env` is concrete (account from `CDK_DEFAULT_ACCOUNT`, region us-east-1) so `from_lookup` resolves → `cdk.context.json` (git-tracked)
- [x] Docker Lambda (+ECR via asset); EventBridge `rate(30 minutes)`; least-privilege IAM (bucket read/write, secret read)
- [x] justfile `deploy` (runs `build` first); `BucketDeployment` with `prune: false`; CfnOutputs
- [ ] **Actually run `cdk deploy`** — needs AWS creds, Docker, and the `hackergist/anthropic-api-key` secret created out of band

## Edge cases
- Same story on both feeds (dedupe by HN id)
- Non-HTML links (PDF, video, image, GitHub, tweets), paywalled, or dead URLs
- Ask / Show / text HN posts with no external URL → gist the HN text
- Very long articles (truncate before summarizing); non-English content
- LLM failure / rate limits → graceful fallback to HN title + snippet
- Canonical-URL matching (tracking params, http vs https, trailing slashes)
- Cost guardrails on summarization (cap per run; reuse gists aggressively)
- Missing points/timestamp → hotness falls back gracefully (treat as low score)

## Etiquette / legal
- Link directly to source (as designed) + link to the HN discussion
- Polite fetching (conditional GET, rate-limit, identify UA); cache to avoid re-hitting sites
- Short, factual gists; attribute source domain clearly; don't republish full article text
- Respect hnrss / HN usage norms

## Nice-to-haves (later)
- [ ] Also emit a gisted RSS/Atom feed (meta touch)
- [ ] Search / filter by domain or topic
- [ ] Topic tags or clustering
- [ ] "Why it's trending" (points / velocity)
- [ ] Save / read-later; keyboard nav
- [ ] Gisted email digest
- [ ] Iterate on the app icon (when Aaron has time)

## Decisions log
- **Frontend**: SvelteKit (Svelte 5) + adapter-static, Tailwind v4, PWA via @vite-pwa/sveltekit; pnpm; Node via nvm (`lts/*`).
- **Backend / pipeline**: Python 3.14 (newest Lambda-supported, AL2023), pyenv + poetry, Docker Lambda (`public.ecr.aws/lambda/python:3.14`).
- **LLM**: Claude Haiku via the **direct Anthropic API** (not Bedrock), `anthropic` SDK; key in Lambda env / Secrets Manager, backend-only.
- **Display / sort**: show the **full current union** (no top-N cap), ordered by a **bespoke hotness score** = f(newness, HN points), computed at render from stored `points` + `published`; formula to design/tune (start from HN's ranking).
- **Storage**: a `FileSystem` abstraction wired via `injector` — `LocalFileSystem` (`.data/`) locally, `S3FileSystem` (bucket) in Lambda, chosen by env; the pipeline talks only to the interface.
- **Local dev**: `.data/` simulates the bucket (gitignored); `just index` runs the pipeline → `.data/data.json`, `just serve` runs the SvelteKit dev server reading it.
- **Data**: single `data.json` = union of parameterless frontpage + best; gists reused by HN id; pruned when out of both feeds.
- **Hosting**: S3 + CloudFront; SPA fetches same-origin `/data.json`.
- **DNS / TLS**: hackergist.dev already lives in Route 53; CDK **looks up** the existing hosted zone (`HostedZone.fromLookup`) and adds alias A/AAAA records to CloudFront; ACM cert in us-east-1, DNS-validated. Stack env must be concrete for the lookup.
- **App icon**: Claude-designed MVP — original and serviceable, **not a placeholder**. Minimal single-glyph mark, one accent color, maskable-safe (direction: a bold condensed glyph evoking *gist / hacker* — e.g., a terminal-style `›` or a geometric `g`). SVG source + generated PNG sizes (192 / 512 / 512-maskable / apple-touch-180 / favicon); dark tile + accent theme colors, tunable. Produced at build time; iterate later.
- **Infra**: AWS CDK (Python); EventBridge schedule (~30 min). (SAM considered, not chosen.)
- **Monorepo** + justfile for dev QoL.
- *Open/tuning: gist prompt wording + token budget; refresh cadence; hotness formula.*
