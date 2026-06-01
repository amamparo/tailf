"""Dependency injection wiring via the ``injector`` library.

Binds three things:

- :class:`~tailf.config.Config` (singleton, from env),
- :class:`~tailf.filesystem.FileSystem` — ``S3FileSystem`` inside Lambda
  (``AWS_LAMBDA_FUNCTION_NAME`` present) else ``LocalFileSystem(.data/)``,
- an Anthropic client, provided lazily so import never requires a key.

Use :func:`build_injector` to get a fully wired ``Injector``.
"""

from __future__ import annotations

import os

from injector import Binder, Injector, Module, provider, singleton

from .config import Config
from .filesystem import FileSystem, LocalFileSystem, S3FileSystem
from .sources import FeedSource, HackerNewsSource, LobstersSource, SourceRegistry


def _in_lambda(environ: object | None = None) -> bool:
    """True when running inside AWS Lambda (selects the S3 backend)."""
    env = os.environ if environ is None else environ  # type: ignore[assignment]
    return bool(env.get("AWS_LAMBDA_FUNCTION_NAME"))  # type: ignore[union-attr]


class TailfModule(Module):
    """Binds Config, FileSystem, and the Anthropic client.

    The environment is captured once at construction so the same module can be
    unit-tested with an injected fake environment.
    """

    def __init__(self, environ: object | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    @singleton
    @provider
    def provide_config(self) -> Config:
        return Config.from_env(self._environ)

    @singleton
    @provider
    def provide_sources(self, config: Config) -> SourceRegistry:
        """The enabled sources: HN always, lobste.rs when configured on."""
        sources: list[FeedSource] = [HackerNewsSource(config)]
        if config.lobsters_enabled:
            sources.append(LobstersSource(config))
        return SourceRegistry(sources=sources)

    @singleton
    @provider
    def provide_filesystem(self, config: Config) -> FileSystem:
        if _in_lambda(self._environ):
            bucket = config.bucket
            if not bucket:
                raise RuntimeError(
                    "TAILF_BUCKET must be set when running in Lambda "
                    "(AWS_LAMBDA_FUNCTION_NAME is present)."
                )
            import boto3

            return S3FileSystem(bucket=bucket, client=boto3.client("s3"))
        return LocalFileSystem(root=config.local_root)

    def configure(self, binder: Binder) -> None:
        # The Anthropic client is bound here rather than via a @provider method
        # so that `injector` does NOT evaluate an "Anthropic" return annotation
        # at install time (it calls get_type_hints on every provider, which
        # would fail for a forward reference, or force an eager import).
        #
        # The `anthropic` package is imported lazily and only when present: the
        # model/store/fetch tests never request the client and don't install it.
        # The binding's factory defers `Anthropic()` construction (and thus the
        # ANTHROPIC_API_KEY lookup) until the client is first resolved.
        try:
            from anthropic import Anthropic
        except ImportError:  # pragma: no cover - anthropic absent in some tests
            return
        binder.bind(Anthropic, to=lambda: Anthropic(), scope=singleton)


def build_injector(environ: object | None = None) -> Injector:
    """Return an :class:`Injector` wired with :class:`TailfModule`."""
    return Injector([TailfModule(environ=environ)])
