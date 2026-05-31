# hackergist ingest + gist pipeline — AWS Lambda (Docker image).
#
# Base is the AL2023 Python 3.14 Lambda runtime image (use dnf, not yum).
# Build context is the `backend/` directory (the CDK builds from here), so all
# COPY paths are relative to backend/. The package lives at backend/hackergist
# and is copied to the Lambda task root as `hackergist`.
FROM public.ecr.aws/lambda/python:3.14

# The runtime deps, installed straight into the Lambda task root. Kept in sync
# with the [tool.poetry.dependencies] (main group) in the root pyproject.toml.
# Pinning is owned by the lockfile / pyproject; here we install the named libs.
RUN dnf -y update && dnf clean all

RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir --target "${LAMBDA_TASK_ROOT}" \
      "httpx" \
      "trafilatura" \
      "pypdf" \
      "boto3" \
      "injector" \
      "anthropic" \
      "python-dotenv" \
      "tqdm" \
      "playwright"

# --- Headless Chromium for the JS-render fallback (render.py) --------------
# Running Playwright's Chromium in Lambda is finicky; this is a best-effort
# setup and likely needs tuning on first real deploy: give the function >= 2GB
# memory and ample /tmp (Chromium writes its profile there), and adjust the dep
# list below if the browser fails to launch. To turn the fallback off entirely,
# set HACKERGIST_RENDER_ENABLED=false (the pipeline then degrades to title-only
# for JS pages, no browser needed).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN dnf -y install \
      nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libxkbcommon \
      libXcomposite libXdamage libXext libXfixes libXrandr mesa-libgbm pango \
      cairo alsa-lib gtk3 \
 && dnf clean all
# playwright was installed with --target above, so put it on PYTHONPATH for the
# CLI; this downloads Chromium into PLAYWRIGHT_BROWSERS_PATH.
RUN PYTHONPATH="${LAMBDA_TASK_ROOT}" python -m playwright install chromium

# Application code: backend/hackergist -> ${LAMBDA_TASK_ROOT}/hackergist
COPY hackergist ${LAMBDA_TASK_ROOT}/hackergist

# EventBridge -> this handler on a schedule.
CMD ["hackergist.handler.handler"]
