# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: implemented & locally verified; not yet deployed

All three subsystems build and pass locally: `poetry install` + `just test` → **40 passed**; `just lint` → ruff + `svelte-check` clean; `just build` → static site + PWA service worker; the CDK stack **synthesizes** to 21 resources (verified against `aws-cdk-lib` 2.257). `poetry.lock` and `frontend/pnpm-lock.yaml` are committed. What remains: a **live pipeline run** (`just index` — needs `ANTHROPIC_API_KEY` + network; never executed against the real feeds/LLM) and an actual **`cdk deploy`** (needs AWS creds, Docker, and the `hackergist/anthropic-api-key` secret created out of band).

### Environment gotchas (already handled, but worth knowing)
- `.python-version` pins **3.14.3** (the installed patch). Poetry can pick a wrong interpreter because `python` on PATH may be an older pyenv version — `just setup` runs `poetry env use "$(pyenv which python3.14)"` first to force 3.14.
- The frontend uses **pnpm via corepack** (`corepack enable pnpm`). pnpm 10+ blocks native build scripts; `frontend/pnpm-workspace.yaml` allow-lists `esbuild` + `sharp`.
- `cdk` is invoked from the **repo root** (per the root `cdk.json`), so the stack resolves the Docker/asset directories as **absolute** paths anchored to the stack file — not `../backend` relative to cwd.

**[TODO.md](TODO.md) is the source of truth** for *intent* — the resolved architecture, locked decisions log, edge cases, and open tuning items (gist prompt wording, refresh cadence, hotness formula). Read it before changing behavior.

**Keep this CLAUDE.md and [README.md](README.md) current as the repo evolves** — correct any decision that changes and prune anything that no longer holds. A stale doc is worse than none.

## What this is

hackergist.dev — a static, installable PWA that aggregates the top Hacker News stories, each with a 1–2 sentence AI "gist" of the **linked article** (not the HN thread). Titles link to the source; a secondary link goes to the HN discussion.

## Architecture (big picture)

A scheduled batch pipeline writes one JSON file; a static frontend renders it. There is no application server.

```
EventBridge (rate 30 min) → Lambda (Docker, Python 3.14)
    fetch both hnrss feeds → union+dedupe by HN id → diff vs current data.json
    → for NEW stories only: fetch article → extract text → Claude Haiku gist
    → reuse existing gists by HN id → write merged data.json (via FileSystem → S3)
CloudFront → serves the static SvelteKit PWA + data.json (same-origin)
Browser → fetches /data.json on load; service worker caches it for offline/installed use
```

Three top-level pieces in one monorepo:
- `backend/` — Python pipeline (package `hackergist`) + `Dockerfile` + `tests/`.
- `frontend/` — SvelteKit 5 static PWA (Tailwind v4, `@vite-pwa/sveltekit`).
- `infra/` — AWS CDK (Python): `app.py` + `hackergist_stack.py`. `cdk.json` lives at the **repo root** (`app: "python infra/app.py"`); there is intentionally only one.

### Backend module map (`backend/hackergist/`)
`config.py` (all tunables from `HACKERGIST_*` env, with defaults) · `models.py` (`Story`, `Gist`, `DataFile` + `to_dict`/`from_dict`, `canonical_url`, `domain_of`) · `filesystem.py` (`FileSystem` ABC, `LocalFileSystem`, `S3FileSystem`) · `di.py` (`injector` module + `build_injector`) · `fetch.py` (both feeds → union/dedupe) · `extract.py` (static article text + fallbacks; `extract_article_from_html` is shared with the render path) · `render.py` (headless-Chromium fallback for JS/SPA pages) · `summarize.py` (Anthropic gist) · `store.py` (read/merge/prune/write) · `pipeline.py` (two-phase orchestration: static gist → render fallback) · `handler.py` (Lambda entry) · `cli.py` (`python -m hackergist.cli`, used by `just index`).

## Non-obvious decisions (easy to get wrong — honor these)

- **One `data.json` is the entire data layer.** It holds the *union of the two parameterless hnrss feeds* (frontpage + best, ≈50–60 stories). No database. Frontend fetches it same-origin. Stories that drop out of **both** feeds are pruned. The exact shape is the contract — keep `backend/hackergist/models.py` `to_dict`, `frontend/src/lib/types.ts`, and both sample `data.json`s in lockstep.
- **Gists are reused by HN id; only new stories are summarized.** Never re-summarize a story that's still present — this reuse is the cost control (every new story gets gisted each run; there is no per-run cap).
- **`gist.kind`** ∈ `article | hn_text | readme | pdf | title_only`. `gist` is `null` **only** when gisting genuinely failed (an API error) — the frontend then shows title-only. A content-less post still gets a `title_only` gist, not `null`.
- **Hotness sort is computed client-side at render**, not stored in `data.json`. Each story carries raw `points` + `published`; `frontend/src/lib/hotness.ts` computes `(points-1)/(age_hours+2)^1.8` and a `now` clock ticks every 60s so it decays live. No top-N cap. `config.hotness_gravity` on the backend is documentation-only.
- **The UI shows only stories WITH a gist** (`+page.svelte` filters `gist != null`); gist-less ones (unreadable sources) are hidden but the backend keeps retrying them each run. Cards are title → image → gist → footer. The `image` field (`og:image`/`twitter:image`, captured during extraction by `extract._extract_image`, reused by `hn_id` like the gist) renders when present, else the card is image-less. The footer favicon is fetched client-side from Google's favicon service keyed on `domain` (no backend storage). The footer shows domain · points · age · comments — **no author byline** (dropped: least useful, and it pushed the footer to a second line on mobile).
- **Feed freshness in the installed PWA.** `data.json` is cached **NetworkFirst** (fresh online, cached offline), but the SPA only fetched it once per page load — so a reopened/backgrounded standalone PWA showed stale data. `+page.svelte` now does a *soft* refetch (keeps the current cards on screen; no skeleton; on failure keeps what's shown) on `visibilitychange`/`pageshow` when the data is older than `STALE_AFTER_MS` (60s). `PullToRefresh.svelte` adds a touch pull-to-refresh (window-level touch listeners since the body is the scroll container; non-passive `touchmove` to `preventDefault` native overscroll only once a downward pull at scrollTop 0 is committed) with an iOS-style **rubber-band** drag (`rubberBand()`: asymptotic toward `PULL_LIMIT`, never a hard clamp). `app.css` sets `overscroll-behavior-y: contain` so the browser's own pull-to-refresh doesn't fight it, makes `<img>` non-selectable/undraggable + `-webkit-tap-highlight-color: transparent` (so a thumb brushing a thumbnail mid-scroll can't leave a stuck green `::selection` tint). `ScrollToTop.svelte` shows a "Back to top" pill after scrolling past ~500px; it does a fast distance-independent `requestAnimationFrame` scroll (respecting `prefers-reduced-motion`) then soft-reloads — rendered *outside* `PullToRefresh` so the pull `transform` doesn't re-anchor its `position: fixed`. Loads are coalesced via an `inFlight` guard so the initial/foreground/pull/back-to-top paths can't overlap. **A failed fetch (≥4xx, network drop, bad JSON) never replaces cards already on screen** — `load`'s catch only surfaces the error state when `data` is still null (nothing to show yet).
- **LLM = Claude Haiku via the direct Anthropic API** (`anthropic` SDK), model id `claude-haiku-4-5` — **not Bedrock**. The SDK reads `ANTHROPIC_API_KEY` from the env. **Locally**, `cli.py` calls `load_dotenv(find_dotenv(usecwd=True))` so a gitignored `.env` at the repo root supplies `ANTHROPIC_API_KEY` (and any `HACKERGIST_*` overrides) for `just index`; see `.env.example`. **In Lambda**, the CDK **creates** the Secrets Manager secret (`hackergist/anthropic-api-key`), **seeding it from `ANTHROPIC_API_KEY` in `.env`** at synth time (`app.py` loads `.env`), and injects it into the function as a `{{resolve:secretsmanager}}` dynamic reference. Tradeoff: the key lands as plaintext in the synthesized template (`cdk.out`, gitignored) — accepted for this solo project. `handler.py` never imports `cli.py`, so dotenv isn't used at runtime there. `summarize.py` caches the system prompt and degrades to `None` on API errors.
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
| `just serve`  | `cd frontend && pnpm dev` (dev middleware serves `.data/data.json`, falling back to `frontend/static/data.json`, at `/data.json`) |
| `just build`  | `cd frontend && pnpm build` → `frontend/build/` |
| `just synth`  | depends on `build`; `poetry run cdk synth` |
| `just deploy` | depends on `build`; `poetry run cdk deploy --all` |
| `just lint`   | `poetry run ruff check .` + `cd frontend && pnpm run check` |
| `just fmt`    | `poetry run ruff format .` |
| `just test`   | `poetry run pytest` |
| `just icons`  | `cd frontend && pnpm run gen-icons` (re-rasterize PNGs from the SVG sources via sharp) |

`.data/` is a **gitignored local stand-in for the S3 bucket** (`.data/.gitkeep` is tracked; a sample `data.json` ships there for `just serve` before the first `just index`).

`pyproject.toml` is a single poetry project: package `hackergist` from `backend/`, groups **main** (`httpx`, `feedparser`, `trafilatura`, `pypdf`, `boto3`, `injector`, `anthropic`, `python-dotenv`, `tqdm`, `playwright`), **infra** (`aws-cdk-lib`, `constructs`), **dev** (`pytest`, `ruff`). `playwright` also needs its browser: `just setup` runs `playwright install chromium`, and the Dockerfile installs Chromium + AL2023 libs (finicky in Lambda — see the Dockerfile; disable with `HACKERGIST_RENDER_ENABLED=false`).

## Edge cases the pipeline handles

Non-HTML links (PDF via `pypdf` → `kind=pdf`; GitHub repo → raw README → `kind=readme`), Ask/Show/text posts with no external URL (`url=null`, gist the HN text → `kind=hn_text`), very long articles (truncated to `HACKERGIST_ARTICLE_CHAR_BUDGET`). **JS/SPA pages**: static extraction rejects "enable JavaScript" shells / too-thin output (`extract._is_unusable_article`), then the render fallback retries with headless Chromium — that recovers most SPAs (~half of the no-gist set in practice). **No useful gist** (`gist=null`, frontend shows the title alone) when a page is genuinely unreadable (hard paywall like WSJ/Reuters, X/Twitter login wall, dead link), when the model replies `SKIP` to junk/boilerplate, or on an LLM error. We **never** gist the title alone — a title restatement adds nothing. Dedupe by HN id and canonical URL (strip tracking params, normalize http/https + trailing slash).
