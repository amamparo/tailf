# TODO — hackergist

*hackergist.dev — a public, installable site that aggregates top Hacker News posts, each with a 1–2 sentence "gist." Titles link straight to the source URL; a secondary link goes to the HN discussion.*

## Problem
Skimming Hacker News means opening a dozen tabs just to learn what each link *is*. hackergist shows the day's top HN stories, each with a one- or two-sentence gist of the **linked content**, so you can decide what's worth a click without the round trip.

## Sources & data universe
- Frontpage — https://hnrss.org/frontpage  ·  Best — https://hnrss.org/best
- The persisted/displayed universe = the **union of the parameterless responses from both feeds** (≈50–60 unique stories). Small enough that all frontend data lives in a **single `data.json` in S3**.
- hnrss item `<link>` = the article URL; the HN comments URL is provided separately in the item.

## Architecture (resolved)
EventBridge (~every 30 min) → **Lambda (Docker, Python 3.14)**: fetch both feeds → union + dedupe by HN id → diff vs the current `data.json` → for *new* stories: fetch the article → extract main text → summarize to ≤2 sentences (LLM); reuse existing gists for stories already present → write the merged **`data.json` to S3** → **CloudFront** serves the static **SvelteKit PWA** + `data.json` (same-origin) → installable on phone.

- `data.json` holds the current union, with gists. Stories that drop out of *both* feeds are pruned; gists are reused by HN id, so nothing is re-summarized while a story is still present. (If re-entry churn ever costs meaningfully, add a small gist cache — not needed initially.)
- The SPA fetches `/data.json` on load and the service worker caches it for offline / installed use.

## Stack (resolved)
**Frontend** — SvelteKit (Svelte 5) + `adapter-static` (fully prerendered, no server); Tailwind CSS v4 (`@tailwindcss/vite`); PWA via `@vite-pwa/sveltekit` (manifest + service worker → installable). Clean, minimal, fully responsive, dark mode. Node via **nvm** (`.nvmrc` → `lts/*`), package manager **pnpm** (fast, strict, first-class with Vite/SvelteKit; npm = fallback).

**Backend / pipeline** — Python **3.14** (newest AWS Lambda-supported; AWS labels it the latest LTS), managed with **pyenv** + **poetry**. Packaged as a **Docker image Lambda** from `public.ecr.aws/lambda/python:3.14` (AL2023 → use `dnf`, not `yum`). Initial libs: `httpx`, `feedparser`, `trafilatura` (extraction), `boto3`, + the chosen LLM SDK.

**Infra** — *(proposed)* **AWS CDK (Python)**, to match the backend toolchain: S3 (static site + `data.json`), CloudFront + ACM cert, the Docker Lambda (+ ECR), EventBridge schedule. (Alt: AWS SAM — simpler, less flexible.)

**Repo** — single monorepo; **justfile** at root for dev QoL (setup, build, deploy, run-pipeline-locally, lint, test).

## Repo layout (proposed)
```
hackergist/
├── justfile                # tasks: setup / dev / build / deploy / pipeline / lint / test
├── .python-version         # pyenv → 3.14.x
├── .nvmrc                  # nvm  → lts/*
├── pyproject.toml          # poetry; groups: main (runtime), infra (cdk), dev (pytest/ruff)
├── poetry.lock
├── README.md  ·  TODO.md
├── backend/
│   ├── hackergist/         # pipeline package
│   │   ├── fetch.py        # pull both hnrss feeds, parse, union, dedupe
│   │   ├── extract.py      # article main-text extraction (+ fallbacks)
│   │   ├── summarize.py    # LLM gist (≤2 sentences)
│   │   ├── store.py        # read / merge / write data.json in S3
│   │   ├── models.py       # Story / Gist
│   │   └── handler.py      # Lambda entrypoint
│   ├── Dockerfile          # FROM public.ecr.aws/lambda/python:3.14
│   └── tests/
├── infra/                  # CDK (Python): S3, CloudFront, Lambda(Docker), EventBridge, ECR
│   └── app.py
└── frontend/               # SvelteKit static PWA
    ├── package.json  ·  svelte.config.js (adapter-static)  ·  vite.config.ts (pwa + tailwind)
    ├── src/{routes, lib, app.html, app.css}
    └── static/{manifest, icons, …}
```

## Remaining decisions
1. **LLM for gists** — recommend **Claude Haiku** (cheap, fast, strong at tight factual summaries; fits the ecosystem). Alternatives: `gpt-4o-mini`, a local model. Key in Lambda env / Secrets Manager, backend-only.
2. **IaC** — confirm **CDK (Python)** vs SAM.
3. **Refresh cadence** — default 30 min; tune freely.
4. **Gist prompt** — exact wording + per-article token budget (tune once we see real output).

## Milestones

### M0 — Scaffolding
- [x] Create repo
- [ ] Monorepo skeleton: justfile, pyenv/poetry, nvm/pnpm, SvelteKit + Tailwind + PWA, CDK app, Dockerfile
- [ ] Config: feed URLs, top-N display, refresh interval, model name, S3 bucket / CloudFront

### M1 — Ingest (Python)
- [ ] Fetch both hnrss feeds (concurrent); conditional GET, timeouts, retries
- [ ] Parse → `{hn_id, title, url, comments_url, points, author, published}`; union + dedupe by HN id (+ canonical URL)

### M2 — Gist pipeline (the meat)
- [ ] Fetch linked page → extract main text → truncate to token budget
- [ ] Summarize ≤2 sentences (neutral, technical); store gist + model + timestamp
- [ ] Fallbacks: GitHub → README, PDF → text, video/paywall/dead → title-only or skip; Ask/Show/text → gist the HN text
- [ ] Reuse gists by HN id; only summarize new stories

### M3 — Persist
- [ ] Merge into a single `data.json`; write to S3; prune stories no longer in either feed
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
- **Data**: single `data.json` in S3 = union of parameterless frontpage + best; gists reused by HN id; pruned when out of both feeds.
- **Hosting**: S3 + CloudFront; SPA fetches same-origin `/data.json`.
- **Infra**: CDK (Python) *(pending confirm)*; EventBridge schedule (~30 min).
- **Monorepo** + justfile for dev QoL.
- *Open: LLM model (recommend Claude Haiku); CDK vs SAM.*
