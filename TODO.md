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
- The SPA fetches `/data.json` on load and the service worker caches it for offline / installed use.

## Stack (resolved)
**Frontend** — SvelteKit (Svelte 5) + `adapter-static` (fully prerendered, no server); Tailwind CSS v4 (`@tailwindcss/vite`); PWA via `@vite-pwa/sveltekit` (manifest + service worker → installable). Clean, minimal, fully responsive, dark mode. Node via **nvm** (`.nvmrc` → `lts/*`), package manager **pnpm** (fast, strict, first-class with Vite/SvelteKit; npm = fallback).

**Backend / pipeline** — Python **3.14** (newest AWS Lambda-supported; AWS labels it the latest LTS), managed with **pyenv** + **poetry**. Packaged as a **Docker image Lambda** from `public.ecr.aws/lambda/python:3.14` (AL2023 → use `dnf`, not `yum`). Initial libs: `httpx`, `feedparser`, `trafilatura` (extraction), `boto3`, `injector` (DI), `anthropic` (gists via **Claude Haiku**).

**Infra** — **AWS CDK (Python)**, matching the backend toolchain: S3 (static site + `data.json`), CloudFront + ACM cert, the Docker Lambda (+ ECR), EventBridge schedule.

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
├── infra/                  # CDK (Python): S3, CloudFront, Lambda(Docker), EventBridge, ECR
│   └── app.py
└── frontend/               # SvelteKit static PWA
    ├── package.json  ·  svelte.config.js (adapter-static)  ·  vite.config.ts (pwa + tailwind + dev /data.json)
    ├── src/{routes, lib, app.html, app.css}
    └── static/{manifest, icons, …}
```

## Open / to tune
1. **Gist prompt** — exact wording + per-article token budget (tune once we see real output).
2. **Refresh cadence** — defaulting to 30 min; tune freely.

## Milestones

### M0 — Scaffolding
- [x] Create repo
- [ ] Monorepo skeleton: justfile, pyenv/poetry, nvm/pnpm, SvelteKit + Tailwind + PWA, CDK app, Dockerfile
- [ ] justfile recipes: `setup`, `index` (run pipeline locally → `.data`), `serve` (run UI locally), `build`, `deploy`, `lint`, `test`
- [ ] `.data/` local S3 stand-in (gitignored); `FileSystem` abstraction + `injector` bindings (LocalFileSystem ↔ S3FileSystem, chosen by env)
- [ ] Config: feed URLs, top-N display, refresh interval, model name, S3 bucket / CloudFront

### M1 — Ingest (Python)
- [ ] Fetch both hnrss feeds (concurrent); conditional GET, timeouts, retries
- [ ] Parse → `{hn_id, title, url, comments_url, points, author, published}`; union + dedupe by HN id (+ canonical URL)

### M2 — Gist pipeline (the meat)
- [ ] Fetch linked page → extract main text → truncate to token budget
- [ ] Summarize ≤2 sentences via Claude Haiku (neutral, technical); store gist + model + timestamp
- [ ] Fallbacks: GitHub → README, PDF → text, video/paywall/dead → title-only or skip; Ask/Show/text → gist the HN text
- [ ] Reuse gists by HN id; only summarize new stories

### M3 — Persist
- [ ] Merge into a single `data.json`; write via the injected FileSystem (S3 in AWS, `.data/` locally); prune stories no longer in either feed
- [ ] Keep fetch metadata (etag / last-seen) for politeness

### M4 — Frontend (SvelteKit PWA)
- [ ] Fetch `/data.json`; list view: **title → source**, gist, meta (domain, points, HN-comments link, time)
- [ ] Frontpage vs Best sections/toggle; per-domain favicon (optional)
- [ ] Responsive + minimal + dark mode; PWA manifest + icons + service worker (installable; offline cache of `data.json`)

### M5 — Deploy (CDK)
- [ ] S3 + CloudFront + ACM for hackergist.dev (HTTPS required — `.dev` is HSTS-preloaded)
- [ ] Docker Lambda + ECR; EventBridge schedule; least-privilege IAM
- [ ] justfile `deploy`; basic logging of gisted / failed

## Edge cases
- Same story on both feeds (dedupe by HN id)
- Non-HTML links (PDF, video, image, GitHub, tweets), paywalled, or dead URLs
- Ask / Show / text HN posts with no external URL → gist the HN text
- Very long articles (truncate before summarizing); non-English content
- LLM failure / rate limits → graceful fallback to HN title + snippet
- Canonical-URL matching (tracking params, http vs https, trailing slashes)
- Cost guardrails on summarization (cap per run; reuse gists aggressively)

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

## Decisions log
- **Frontend**: SvelteKit (Svelte 5) + adapter-static, Tailwind v4, PWA via @vite-pwa/sveltekit; pnpm; Node via nvm (`lts/*`).
- **Backend / pipeline**: Python 3.14 (newest Lambda-supported, AL2023), pyenv + poetry, Docker Lambda (`public.ecr.aws/lambda/python:3.14`).
- **LLM**: Claude Haiku via the `anthropic` SDK; key in Lambda env / Secrets Manager, backend-only.
- **Storage**: a `FileSystem` abstraction wired via `injector` — `LocalFileSystem` (`.data/`) locally, `S3FileSystem` (bucket) in Lambda, chosen by env; the pipeline talks only to the interface.
- **Local dev**: `.data/` simulates the bucket (gitignored); `just index` runs the pipeline → `.data/data.json`, `just serve` runs the SvelteKit dev server reading it.
- **Data**: single `data.json` = union of parameterless frontpage + best; gists reused by HN id; pruned when out of both feeds.
- **Hosting**: S3 + CloudFront; SPA fetches same-origin `/data.json`.
- **Infra**: AWS CDK (Python); EventBridge schedule (~30 min). (SAM considered, not chosen.)
- **Monorepo** + justfile for dev QoL.
- *Open/tuning: gist prompt wording + token budget; refresh cadence.*
