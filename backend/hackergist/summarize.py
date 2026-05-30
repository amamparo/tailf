"""Summarize a story's source into a one-line gist via Claude Haiku.

Uses the DIRECT Anthropic API (``anthropic`` SDK), model ``claude-haiku-4-5``.
The SYSTEM prompt is a frozen instruction block with a ``cache_control``
breakpoint so it is cached across the many gist calls in a run; the volatile
per-article content goes in the USER message, after the cached prefix.

Design choices:

- ``max_tokens`` is small (~70) — a single short sentence fits comfortably.
- We return ``None`` (gist-less; the frontend shows just the title) when there
  is no usable source content, when the model declines an empty/junk excerpt by
  replying ``SKIP``, or on a rate-limit / API / network error. We never gist the
  TITLE alone — a title restatement adds nothing for a reader who's already read
  it. So ``gist=None`` simply means "no useful gist for this story this run".
- ``max_tokens`` is small (~70) — a single short sentence fits comfortably.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import anthropic

from .config import Config
from .extract import Extracted
from .models import Gist, utc_now_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import Anthropic

logger = logging.getLogger(__name__)

# Frozen system prompt — stable bytes so the cache prefix is reused every call.
SYSTEM_PROMPT = (
    "You write a one-line gist of linked content for a Hacker News reader who "
    "has ALREADY read the post title. Your only job: tell them, in a few words, "
    "what the content adds BEYOND the title so they can decide whether to open "
    "it. The source is an article, README, or HN self-post — never the HN "
    "discussion.\n"
    "Rules:\n"
    "- ONE sentence, ideally under 20 words. Never a paragraph. A short second "
    "sentence ONLY if truly essential.\n"
    "- Do NOT restate, quote, or paraphrase the title — assume it's already "
    "read. Skip what the title conveys and surface the specific new payload: "
    "the finding, number, mechanism, claim, or twist.\n"
    "- Concrete and specific, never generic. No marketing, hype, opinion, or "
    'first person. Do not begin with "This", "The article", or "The author".\n'
    "- NEVER comment on the source itself — its loading, extraction, "
    "availability, paywalls, cookies, or JavaScript. Write only about the "
    "subject matter.\n"
    "- If the excerpt has no real information about the SUBJECT — e.g. an "
    "error, login, or access page; a cookie/consent wall; an \"enable "
    "JavaScript\" shell; a bare site or page name; or just navigation or "
    "boilerplate — output exactly: SKIP, and nothing else. Do NOT fall back to "
    "summarizing the title; a title restatement is worse than no gist.\n"
    "- Output only the gist text — no preamble, quotes, or labels."
)

# Human-readable label for each kind, fed into the user message for context.
_KIND_LABEL: dict[str, str] = {
    "article": "article",
    "readme": "project README",
    "hn_text": "Hacker News self-post",
    "pdf": "PDF document",
}


def summarize(
    client: Anthropic,
    title: str,
    extracted: Extracted | None,
    config: Config,
) -> Gist | None:
    """Return a :class:`Gist` for ``title`` + ``extracted``, or ``None``.

    ``None`` means the gist could not be produced (the API call failed) — the
    caller leaves the story gist-less. When there is no extractable source
    content we still summarize from the title alone (``kind="title_only"``),
    matching the contract where ``gist`` is null only on genuine failure.
    """
    # No usable source content -> no gist. We never summarize the title alone:
    # a title restatement adds nothing (the reader has already read the title).
    if extracted is None or not extracted.text.strip():
        return None

    label = _KIND_LABEL.get(extracted.kind, "source")
    user_content = (
        f"Title: {title}\n\n"
        f"Source ({label}) excerpt:\n{extracted.text}\n\n"
        "Write the gist now."
    )

    try:
        response = client.messages.create(
            model=config.model,
            max_tokens=config.gist_max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        # RateLimitError / OverloadedError / 5xx / connection issues all land
        # here — degrade gracefully to no gist for this story this run.
        logger.warning("gist failed for %r: %s", title[:80], exc)
        return None

    text = _first_text(response)
    # Empty response, or the model declined with SKIP because the excerpt had
    # no real information about the subject -> no gist (frontend shows the title).
    if not text or text.strip().upper() == "SKIP":
        return None

    return Gist(
        text=text,
        model=config.model,
        generated_at=utc_now_iso(),
        kind=extracted.kind,
    )


def _first_text(response: object) -> str:
    """Extract the first text block from a Messages API response."""
    content = getattr(response, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "text":
            return (getattr(block, "text", "") or "").strip()
    return ""
