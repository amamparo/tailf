#!/usr/bin/env python3
"""CDK app entrypoint for hackergist.dev.

Synthesizes a single stack (``HackergistStack``) that hosts the static
SvelteKit PWA + ``data.json`` on S3/CloudFront and runs the Python pipeline
as a scheduled Docker-image Lambda.

Why the env is pinned to us-east-1 with a concrete account:

* CloudFront requires its ACM certificate to live in ``us-east-1``. The
  simplest correct setup is to run the *entire* stack there so the cert is
  colocated with the distribution (no cross-region cert stack needed).
* ``route53.HostedZone.from_lookup`` is a context lookup that runs at synth
  time against a real account/region; it only resolves when the stack ``env``
  is a concrete account + region (the result is cached into
  ``cdk.context.json``). An env-agnostic stack would fail the lookup.

The account comes from the ``CDK_DEFAULT_ACCOUNT`` environment variable
(populated by the CDK CLI from your current AWS credentials). If it is
missing we still construct the App so ``cdk ls`` / IDE tooling works, but
``cdk synth`` / ``cdk deploy`` need the account set for the zone lookup to
resolve.

Run order for a deploy (see the root justfile):

    just build          # produces frontend/build (consumed by BucketDeployment)
    just deploy         # runs `cdk deploy` from the repo root (depends on build)

The ``ANTHROPIC_API_KEY`` is loaded from the local ``.env`` (gitignored) at
synth time and used to *seed* the Secrets Manager secret
(``hackergist/anthropic-api-key``) that the stack creates. It must be present
for synth/deploy; export it or put it in ``.env`` (see ``.env.example``).
NOTE: this writes the key as plaintext into the synthesized template — an
accepted tradeoff for this solo project (see the stack's secret comment).
"""

import os
import sys

from dotenv import find_dotenv, load_dotenv

# Make `from hackergist_stack import ...` resolve no matter the CWD the CDK CLI
# runs us from. The canonical cdk.json lives at the repo root and runs
# `python infra/app.py` (CWD = repo root), so this file's directory is not on
# sys.path by default. Putting it first makes the sibling-module import work
# whether the app is invoked from the repo root or directly from infra/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aws_cdk as cdk  # noqa: E402
from hackergist_stack import HackergistStack  # noqa: E402

# Domain is fixed for this project; the matching Route 53 hosted zone already
# exists and is looked up (never created) inside the stack.
DOMAIN_NAME = "hackergist.dev"

# CloudFront + ACM must be colocated in us-east-1 (see module docstring).
REGION = "us-east-1"

# Load the gitignored .env (searched from the cwd upward — the repo root under
# `just deploy`) so the Anthropic key (and CDK_DEFAULT_ACCOUNT, if set there) is
# available at synth time. No-op when there's no .env; real exports still win.
load_dotenv(find_dotenv(usecwd=True))

app = cdk.App()

# CDK_DEFAULT_ACCOUNT is injected by the CDK CLI from the active credentials.
# May be None under bare tooling; the lookup-backed stack needs it for synth.
account = os.environ.get("CDK_DEFAULT_ACCOUNT")

# The stack seeds the Secrets Manager secret with this value, so it must exist
# at synth time. Fail fast (rather than silently shipping a placeholder secret).
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not anthropic_api_key:
    raise SystemExit(
        "ANTHROPIC_API_KEY is not set — the stack seeds the Secrets Manager "
        "secret with it. Put it in .env (see .env.example) or export it before "
        "`cdk synth` / `cdk deploy`."
    )

HackergistStack(
    app,
    "HackergistStack",
    # Concrete env is REQUIRED for HostedZone.from_lookup to resolve. If
    # `account` is None the App still builds, but synth/deploy will need it.
    env=cdk.Environment(account=account, region=REGION),
    domain_name=DOMAIN_NAME,
    anthropic_api_key=anthropic_api_key,
)

app.synth()
