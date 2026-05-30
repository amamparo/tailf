"""Runtime configuration, read from the environment with sane defaults.

All tunables live here so the rest of the package can stay declarative. The
canonical way to build one is :meth:`Config.from_env`, which the DI module
binds as a singleton.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The two hnrss feeds whose union forms the entire data universe. We ask for
# count=50, but hnrss only exposes what's actually on each page: ~39 for
# frontpage and 30 for best (count=100 returns the same). So the union after
# dedupe is ~60 stories, not 100 — these endpoints simply don't have more.
FRONTPAGE_FEED_URL = "https://hnrss.org/frontpage?count=50"
BEST_FEED_URL = "https://hnrss.org/best?count=50"

# Default polite user agent; identifies the crawler to feeds and article hosts.
DEFAULT_USER_AGENT = (
    "hackergist/0.1 (+https://hackergist.dev; aggregator with AI gists)"
)

# The locked LLM model id — Claude Haiku via the direct Anthropic API.
DEFAULT_MODEL = "claude-haiku-4-5"


@dataclass(frozen=True)
class Config:
    """Immutable pipeline configuration.

    Built once per run via :meth:`from_env`. Fields cover the feed sources,
    the article text budget, the storage target (``bucket`` / ``data_key`` /
    ``local_root``), and a couple of values kept only for reference/parity with
    the frontend.
    """

    # --- Feeds -----------------------------------------------------------
    frontpage_feed_url: str = FRONTPAGE_FEED_URL
    best_feed_url: str = BEST_FEED_URL

    # --- Scheduling (informational; the real cadence lives in EventBridge) -
    refresh_minutes: int = 60

    # --- LLM / gisting ----------------------------------------------------
    model: str = DEFAULT_MODEL
    #: Max characters of extracted article text handed to the model.
    article_char_budget: int = 12_000
    #: Max output tokens for a gist (<= 2 sentences fits comfortably).
    gist_max_tokens: int = 70

    # --- Hotness (reference only; the score is computed CLIENT-SIDE) -------
    #: Gravity exponent in HN's ranking; kept here purely for documentation.
    hotness_gravity: float = 1.8

    # --- HTTP politeness --------------------------------------------------
    user_agent: str = DEFAULT_USER_AGENT
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 2
    #: Bound on concurrent article extractions so we stay polite.
    extract_concurrency: int = 6

    # --- JS rendering fallback (headless Chromium via Playwright) ----------
    #: When True, pages that fail static extraction are re-fetched with a
    #: headless browser so client-side-rendered (SPA) content can be scraped.
    render_enabled: bool = True
    #: Concurrent browser pages (Chromium is memory-heavy; keep this low).
    render_concurrency: int = 3
    #: Per-page navigation timeout for rendering (milliseconds).
    render_timeout_ms: int = 20_000
    #: Extra settle time after load so SPA JS can populate the DOM (ms).
    render_wait_ms: int = 1_500

    # --- Storage ----------------------------------------------------------
    #: S3 bucket name (from ``HACKERGIST_BUCKET``); ``None`` locally.
    bucket: str | None = None
    #: Object key / filename for the single data file.
    data_key: str = "data.json"
    #: Local stand-in directory for the bucket during dev.
    local_root: str = ".data"

    @classmethod
    def from_env(cls, environ: object | None = None) -> Config:
        """Build a :class:`Config` from environment variables.

        Every field has a default, so an empty environment yields a valid
        local-dev config. Numeric fields fall back to the default if the env
        value is missing or unparseable.
        """
        env = os.environ if environ is None else environ  # type: ignore[assignment]

        def _str(key: str, default: str) -> str:
            value = env.get(key)  # type: ignore[union-attr]
            return value if value else default

        def _opt_str(key: str) -> str | None:
            value = env.get(key)  # type: ignore[union-attr]
            return value or None

        def _int(key: str, default: int) -> int:
            raw = env.get(key)  # type: ignore[union-attr]
            if not raw:
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def _float(key: str, default: float) -> float:
            raw = env.get(key)  # type: ignore[union-attr]
            if not raw:
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        def _bool(key: str, default: bool) -> bool:
            raw = env.get(key)  # type: ignore[union-attr]
            if raw is None or raw == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        return cls(
            frontpage_feed_url=_str("HACKERGIST_FRONTPAGE_FEED", FRONTPAGE_FEED_URL),
            best_feed_url=_str("HACKERGIST_BEST_FEED", BEST_FEED_URL),
            refresh_minutes=_int("HACKERGIST_REFRESH_MINUTES", 60),
            model=_str("HACKERGIST_MODEL", DEFAULT_MODEL),
            article_char_budget=_int("HACKERGIST_ARTICLE_CHAR_BUDGET", 12_000),
            gist_max_tokens=_int("HACKERGIST_GIST_MAX_TOKENS", 70),
            hotness_gravity=_float("HACKERGIST_HOTNESS_GRAVITY", 1.8),
            user_agent=_str("HACKERGIST_USER_AGENT", DEFAULT_USER_AGENT),
            http_timeout_seconds=_float("HACKERGIST_HTTP_TIMEOUT", 15.0),
            http_max_retries=_int("HACKERGIST_HTTP_MAX_RETRIES", 2),
            render_enabled=_bool("HACKERGIST_RENDER_ENABLED", True),
            render_concurrency=_int("HACKERGIST_RENDER_CONCURRENCY", 3),
            render_timeout_ms=_int("HACKERGIST_RENDER_TIMEOUT_MS", 20_000),
            render_wait_ms=_int("HACKERGIST_RENDER_WAIT_MS", 1_500),
            extract_concurrency=_int("HACKERGIST_EXTRACT_CONCURRENCY", 6),
            bucket=_opt_str("HACKERGIST_BUCKET"),
            data_key=_str("HACKERGIST_DATA_KEY", "data.json"),
            local_root=_str("HACKERGIST_LOCAL_ROOT", ".data"),
        )

    @property
    def feed_urls(self) -> dict[str, str]:
        """Map of feed name -> URL, in the canonical ``feeds`` order."""
        return {"frontpage": self.frontpage_feed_url, "best": self.best_feed_url}
