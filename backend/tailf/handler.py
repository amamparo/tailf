"""AWS Lambda entrypoint.

The image's ``CMD`` is ``tailf.handler.handler``. EventBridge invokes it
on a schedule; the ``event`` / ``context`` args are unused. We build the DI
injector (which selects the S3 FileSystem when running in Lambda), run the
pipeline, log the summary, and return it.
"""

from __future__ import annotations

import logging
from typing import Any

from .di import build_injector
from .pipeline import run

# AWS Lambda pre-installs a root log handler, so logging.basicConfig() is a
# no-op there and our INFO logs (per-feed counts + run summary) would be
# dropped. Set the root level directly so they reach CloudWatch; fall back to
# basicConfig when running outside Lambda (no handler installed yet).
logging.getLogger().setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: Any = None, context: Any = None) -> dict[str, int]:
    """Run the ingest + gist pipeline once and return the run summary."""
    injector = build_injector()
    summary = run(injector)
    logger.info("tailf run summary: %s", summary)
    return summary
