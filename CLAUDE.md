# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: pre-implementation

There is **no code yet** — only [README.md](README.md) and [TODO.md](TODO.md). The architecture is fully resolved; the work is to scaffold and build it out per the spec.

**[TODO.md](TODO.md) is the source of truth.** It holds the resolved architecture, the locked decisions log, the proposed repo layout, milestones (M0–M5), edge cases, and open tuning items. Read it before starting any work, and keep its milestone checkboxes and decisions log in sync as you build.

**Keep this CLAUDE.md and [README.md](README.md) current as the repo evolves.** They currently describe a planning-stage repo; as code lands, update them so they stay accurate — replace the "pre-implementation" framing with real build/lint/test commands once the `justfile` exists, refresh the README from a one-liner into real setup/usage docs, correct any decision that changes, and prune anything that no longer holds. A stale CLAUDE.md or README is worse than none.

## What this is

hackergist.dev — a static, installable PWA that aggregates the top Hacker News stories, each with a 1–2 sentence AI "gist" of the **linked article** (not the HN thread). Titles link to the source; a secondary link goes to the HN discussion.

## Architecture (big picture)

A scheduled batch pipeline writes one JSON file; a static frontend renders it. There is no application server.

```
EventBridge (~30 min) → Lambda (Docker, Python 3.14)
    fetch both hnrss feeds → union+dedupe by HN id → diff vs current data.json
    → for NEW stories only: fetch article → extract text → Claude Haiku gist
    → reuse existing gists by HN id → write merged data.json (via FileSystem → S3)
CloudFront → serves the static SvelteKit PWA + data.json (same-origin)
Browser → fetches /data.json on load; service worker caches it for offline/installed use
```

Three top-level pieces in one monorepo: `backend/` (Python pipeline + Docker Lambda), `frontend/` (SvelteKit static PWA), `infra/` (AWS CDK in Python).

## Non-obvious decisions (easy to get wrong — honor these)

- **One `data.json` is the entire data layer.** It holds the *union of the two parameterless hnrss feeds* (frontpage + best, ≈50–60 stories). No database. The frontend fetches it same-origin. Stories that drop out of **both** feeds are pruned.
- **Gists are reused by HN id; only new stories are summarized.** Never re-summarize a story that's still present — this is the core cost guardrail. Summarization cost is capped per run.
- **Hotness sort is computed client-side at render**, not baked into `data.json`. Each story stores raw `points` + `published`; the frontend (`lib/hotness.ts`) computes `f(newness, points)` at render so recency keeps decaying between 30-min refreshes. The whole union is shown (no top-N cap).
- **LLM = Claude Haiku via the direct Anthropic API** (the `anthropic` SDK) — **not Bedrock**. Key lives in Lambda env / Secrets Manager, backend-only.
- **All persistence goes through a `FileSystem` abstraction wired with `injector` DI.** `LocalFileSystem` (root `.data/`) locally vs `S3FileSystem` (the bucket) in Lambda, selected by env (`AWS_LAMBDA_FUNCTION_NAME` present → AWS, else `HACKERGIST_ENV`). The pipeline / `store.py` talk **only to the injected interface**, never to disk or `boto3`/S3 directly.
- **CDK looks up the existing Route 53 zone** with `HostedZone.fromLookup(domainName="hackergist.dev")` — do **not** create a hosted zone. Add alias A + AAAA records to the CloudFront distribution.
- **ACM cert must be in us-east-1** (CloudFront requirement); simplest to run the whole stack in us-east-1. The stack `env` must be a **concrete account + region** for `fromLookup` to resolve (it caches into `cdk.context.json`).
- **Lambda is a Docker image**, `FROM public.ecr.aws/lambda/python:3.14` (AL2023 base → use `dnf`, not `yum`).

## Toolchain & commands

Toolchain: **pnpm** (Node via nvm, `.nvmrc` → `lts/*`) for the frontend; **poetry** (Python via pyenv, `.python-version` → 3.14.x) for backend + infra. A root **`justfile`** is the intended dev interface.

> The `justfile` and these recipes are **specified in [TODO.md](TODO.md) but not yet implemented** — create them as part of M0. Planned recipes:

| Recipe        | Purpose |
|---------------|---------|
| `just setup`  | install all toolchains/deps |
| `just index`  | run the pipeline locally (poetry) → writes `.data/data.json` |
| `just serve`  | run the SvelteKit dev server (reads `.data/data.json`, served at `/data.json` via a Vite dev middleware to mirror the prod same-origin fetch) |
| `just build`  | build the static frontend |
| `just deploy` | CDK deploy |
| `just lint`   | lint (ruff for Python) |
| `just test`   | run tests (pytest) |

`.data/` is a **gitignored local stand-in for the S3 bucket** — `data.json` and cached artifacts live there during local dev. Run `just index` to populate it, then `just serve`.

`pyproject.toml` uses poetry groups: **main** (runtime), **infra** (cdk), **dev** (pytest/ruff). Initial runtime libs: `httpx`, `feedparser`, `trafilatura` (extraction), `boto3`, `injector`, `anthropic`.

## Edge cases the pipeline must handle

Non-HTML links (PDF/video/image/GitHub/tweets), paywalled or dead URLs, Ask/Show/text posts with no external URL (gist the HN text instead), very long or non-English articles (truncate before summarizing), and LLM failure / rate limits (fall back to HN title + snippet). Dedupe by HN id and by canonical URL (strip tracking params, normalize http/https + trailing slash). See [TODO.md](TODO.md) "Edge cases" for the full list.
