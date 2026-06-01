"""Local entrypoint — ``poetry run python -m tailf.cli`` (``just index``).

Builds the DI injector (which, outside Lambda, selects ``LocalFileSystem`` at
``.data/``), runs the pipeline, and prints the summary as JSON.
"""

from __future__ import annotations

import json
import logging

from dotenv import find_dotenv, load_dotenv

from .di import build_injector
from .pipeline import run


def main() -> None:
    """Run the pipeline locally and print the summary."""
    # Local convenience: load a gitignored `.env` (searched from the cwd upward,
    # i.e. the repo root under `just index`) so ANTHROPIC_API_KEY and any
    # TAILF_* overrides are available before the injector reads the env.
    # No-op when there's no .env (e.g. real shell exports, CI). Not used in
    # Lambda — that path goes through handler.py and never imports this module.
    load_dotenv(find_dotenv(usecwd=True))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    injector = build_injector()
    summary = run(injector)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
