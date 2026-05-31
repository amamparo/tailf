# hackergist

[hackergist.dev](https://hackergist.dev) — a public, installable site that aggregates the top Hacker News stories, each with a 1–2 sentence AI "gist" of the **linked article** so you can decide what's worth a click without opening a dozen tabs. Titles link straight to the source; a secondary link goes to the HN discussion.

## How it works

A scheduled batch job writes one JSON file; a static PWA renders it. No application server.

```
EventBridge (hourly) → Lambda (Docker, Python 3.14)
    fetch HN topstories + beststories (official Firebase API) → hydrate items → union & dedupe → diff vs current data.json
    → for new stories: fetch article → extract text → Claude Haiku gist (≤2 sentences)
    → reuse existing gists → write merged data.json to S3
CloudFront → serves the SvelteKit PWA + data.json (same-origin)
```

The frontend shows the **entire** current union of both feeds, ordered by a hotness score computed in the browser (so recency keeps decaying between refreshes), and is installable for offline reading.

## Layout

| Path        | What |
|-------------|------|
| `backend/`  | Python ingest + gist pipeline (package `hackergist`) + `Dockerfile` + tests |
| `frontend/` | SvelteKit 5 static PWA (Tailwind v4, vite-pwa) |
| `infra/`    | AWS CDK (Python) — S3 + CloudFront + ACM + Route 53 + Docker Lambda + EventBridge |
| `cdk.json`, `pyproject.toml`, `justfile` | root config (one poetry project covers backend + infra) |

## Quickstart

Prereqs: [pyenv](https://github.com/pyenv/pyenv) (Python 3.14), [poetry](https://python-poetry.org/), [nvm](https://github.com/nvm-sh/nvm) (Node LTS), [pnpm](https://pnpm.io/), [just](https://github.com/casey/just), and (for deploy) Docker + AWS credentials.

```sh
just setup                       # poetry install + pnpm install + cdk CLI
cp .env.example .env             # then set ANTHROPIC_API_KEY in .env (gitignored)
just index                       # run the pipeline locally → .data/data.json
just serve                       # SvelteKit dev server (reads .data/data.json at /data.json)
```

`just index` auto-loads `.env` from the repo root (via python-dotenv) — put `ANTHROPIC_API_KEY` and any `HACKERGIST_*` overrides there; see [.env.example](.env.example). Plain shell exports work too and take precedence.

Other recipes: `just build` (static site → `frontend/build/`), `just test` (pytest), `just lint`, `just fmt`, `just synth` / `just deploy` (CDK; runs `just build` first). Run `just` to list them.

## Deploy notes

- The domain `hackergist.dev` must already exist as a Route 53 hosted zone — CDK looks it up, it does not create it. The stack runs in **us-east-1** (CloudFront requires the ACM cert there).
- The stack **creates** the Secrets Manager secret `hackergist/anthropic-api-key`, seeding it from `ANTHROPIC_API_KEY` in your `.env` (or shell env) at synth time — so the same `.env` you use for `just index` also feeds the deploy. Tradeoff: the key is written as plaintext into the synthesized template (`cdk.out`, gitignored) and the CloudFormation console. If a secret with that name already exists in the account, delete it first (the stack now owns it).
- `cdk synth`/`deploy` need `CDK_DEFAULT_ACCOUNT` + AWS credentials so the zone lookup resolves (cached into the git-tracked `cdk.context.json`).

See [CLAUDE.md](CLAUDE.md) for the architecture details, locked decisions, edge cases, and open tuning items.
