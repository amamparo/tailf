"""hackergist — the ingest + gist pipeline backend.

A scheduled batch pipeline that fetches the union of the two parameterless
hnrss feeds (frontpage + best), summarizes the linked article for each *new*
story via Claude Haiku, and writes a single ``data.json`` through a
``FileSystem`` abstraction (local disk in dev, S3 in Lambda).

The public surface is intentionally small:

- :func:`hackergist.di.build_injector` — wire up ``Config``, ``FileSystem``
  and the Anthropic client.
- :func:`hackergist.pipeline.run` — run one full pass and return a summary.
- :func:`hackergist.handler.handler` — the Lambda entrypoint.
- :func:`hackergist.cli.main` — the local entrypoint (``just index``).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
