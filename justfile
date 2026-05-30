# hackergist — dev recipes. Run `just` to list, `just <recipe>` to run.
# Toolchains: poetry (Python 3.14) for backend/infra, pnpm (Node lts/*) for frontend.

# Default: show available recipes.
default:
    @just --list

# Install all toolchains/deps (Python via poetry, frontend via pnpm, CDK CLI via npm).
# Pin Poetry's venv to the pyenv 3.14 interpreter first — `python` on PATH may be an
# older version (Poetry would otherwise reject it against the ^3.14 constraint).
setup:
    poetry env use "$(pyenv which python3.14)"
    poetry install
    poetry run playwright install chromium   # headless browser for the JS-render fallback
    cd frontend && pnpm install
    npm install -g aws-cdk

# Run the pipeline locally → writes .data/data.json (LocalFileSystem stand-in for S3).
index:
    poetry run python -m hackergist.cli

# Run the SvelteKit dev server (serves .data/data.json at /data.json via dev middleware).
serve:
    cd frontend && pnpm dev

# Build the static, fully-prerendered frontend.
build:
    cd frontend && pnpm build

# Synthesize the CDK CloudFormation templates.
# Depends on `build`: the stack's BucketDeployment resolves ../frontend/build
# as a synth-time asset, so the frontend output must exist first.
synth: build
    poetry run cdk synth

# Build the frontend, then deploy the whole stack (S3 + CloudFront + ACM +
# Route 53 + Docker Lambda + EventBridge). Non-interactive (skips the IAM
# approval prompt) so it's a one-shot `just deploy`. Needs AWS creds, Docker,
# ANTHROPIC_API_KEY (in .env — seeds the secret), and a bootstrapped account.
deploy: build
    poetry run cdk deploy --all --require-approval never
    @echo "Invalidating CloudFront cache (/*) ..."
    DIST=$(aws cloudformation describe-stack-resources --stack-name HackergistStack --region us-east-1 --query "StackResources[?ResourceType=='AWS::CloudFront::Distribution'].PhysicalResourceId" --output text) && aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/*' --region us-east-1

# Lint Python (ruff) and the frontend (svelte-check).
lint:
    poetry run ruff check .
    cd frontend && pnpm run check

# Format Python with ruff.
fmt:
    poetry run ruff format .

# Run the backend test suite.
test:
    poetry run pytest

# Regenerate PWA PNG icons from the SVG source (requires a rasterizer, e.g. rsvg-convert).
icons:
    cd frontend && pnpm run gen-icons
