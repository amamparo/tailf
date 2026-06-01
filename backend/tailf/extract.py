"""Extract main text from a story's source, with per-kind fallbacks.

Returns an :class:`Extracted` (text + kind) or ``None`` when nothing usable
can be obtained — in which case the caller falls back to a title-only gist.

Strategy, in order:

1. No external url -> use the stored self-post text (``kind="self_text"``).
2. GitHub repo url -> fetch the raw README (``kind="readme"``).
3. ``application/pdf`` -> best-effort text extraction via pypdf (``kind="pdf"``).
4. HTML -> trafilatura main-text extraction (``kind="article"``).
5. Anything else / dead / paywalled / video -> ``None``.

All network calls are polite (custom UA, timeout) and swallow errors.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

from .config import Config
from .models import Story

logger = logging.getLogger(__name__)

ExtractKind = Literal["article", "self_text", "readme", "pdf"]

# README candidates to try on the raw.githubusercontent host, in order.
_README_CANDIDATES = (
    "README.md",
    "README.rst",
    "README.txt",
    "readme.md",
    "README",
)
_DEFAULT_BRANCHES = ("main", "master")


@dataclass
class Extracted:
    """Extracted source text, the kind of source, and an optional preview image."""

    text: str
    kind: ExtractKind
    #: Social/preview image (og:image / twitter:image), absolute URL, or None.
    image: str | None = None
    #: The page's own title (og:title / <title>), or None. Used to title a
    #: cross-post whose two sources disagree (see ``models.resolve_title``).
    seo_title: str | None = None


def extract(story: Story, config: Config, client: httpx.Client | None = None) -> Extracted | None:
    """Extract main text for ``story``. Returns ``None`` if nothing usable.

    A shared :class:`httpx.Client` may be passed in to reuse connections
    across a batch; otherwise a short-lived one is created.
    """
    # 1. Ask/Show/text posts: no external url, use the captured self-text.
    if story.url is None:
        if story.self_text:
            return Extracted(text=_truncate(story.self_text, config), kind="self_text")
        return None

    own_client = client is None
    if client is None:
        client = _make_client(config)
    try:
        # 2. GitHub repo -> raw README.
        readme = _try_github_readme(story.url, client, config)
        if readme is not None:
            return readme

        # Fetch the page (or a HEAD-ish GET) to inspect content type.
        try:
            response = client.get(story.url)
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TransportError):
            return None

        content_type = response.headers.get("content-type", "").lower()

        # 3. PDF -> best-effort text extraction via pypdf. If the PDF has no
        #    extractable text (scanned/image-only) we return None so the caller
        #    degrades to a title-only gist rather than emitting an empty body.
        if "application/pdf" in content_type or story.url.lower().endswith(".pdf"):
            pdf_text = _extract_pdf_text(response.content)
            if pdf_text:
                return Extracted(text=_truncate(pdf_text, config), kind="pdf")
            return None

        # 4. Non-HTML (video/image/json/etc.) -> unsupported.
        if content_type and not _looks_like_html(content_type):
            return None

        # 5. HTML article via trafilatura. A JS-shell / too-thin result yields
        #    None here; pipeline.py then retries via the headless-render fallback
        #    (render.py), which feeds the rendered HTML back through
        #    extract_article_from_html below.
        return extract_article_from_html(response.text, story.url, config)
    finally:
        if own_client:
            client.close()


def extract_article_from_html(html: str, url: str, config: Config) -> Extracted | None:
    """Extract article text from raw HTML via trafilatura, or ``None``.

    Shared by the static fetch path (above) and the JS-render fallback in
    ``render.py``. We don't judge content quality here with char-count
    thresholds — we hand over whatever text exists and let the model decide
    (it replies ``SKIP`` for JS shells, error/access pages, and other
    boilerplate; see ``summarize``). That keeps an arbitrary cutoff from
    silently dropping good content or admitting junk.

    Order: prefer the trafilatura-extracted body; if there's none, fall back to
    the page's ``og:description`` (paywalled news / SPAs that block body
    scraping still ship a useful summary there). ``None`` only when the page
    yields no text at all (the caller then leaves it gist-less / retries later).
    """
    metadata = _safe_metadata(html)
    image = _image_from(metadata, url)
    seo_title = _title_from(metadata)

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if text and text.strip():
        return Extracted(
            text=_truncate(text, config), kind="article", image=image, seo_title=seo_title
        )

    description = _description_from(metadata)
    if description:
        return Extracted(
            text=_truncate(description, config), kind="article", image=image, seo_title=seo_title
        )
    return None


def _safe_metadata(html: str) -> object | None:
    """trafilatura page metadata, or ``None`` on any parse failure."""
    try:
        return trafilatura.extract_metadata(html)
    except Exception:  # noqa: BLE001 - metadata parsing must never raise here
        return None


def _image_from(metadata: object | None, url: str) -> str | None:
    """Absolute og:image / twitter:image URL from parsed metadata, or ``None``."""
    image = getattr(metadata, "image", None) if metadata else None
    if not image or not isinstance(image, str):
        return None
    return urljoin(url, image.strip())


def _title_from(metadata: object | None) -> str | None:
    """The page's own title (trafilatura derives it from og:title / <title> /
    <h1>), or ``None``. Used only to title a cross-post whose sources disagree."""
    title = getattr(metadata, "title", None) if metadata else None
    if not title or not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


def _description_from(metadata: object | None) -> str | None:
    """The page's meta description (og:description / description), or ``None``.

    No length gate — a useless one (e.g. a bare "Weave Jobs") is handed to the
    model, which replies ``SKIP`` rather than us guessing a cutoff.
    """
    desc = (getattr(metadata, "description", None) or "").strip() if metadata else ""
    return desc or None


def _make_client(config: Config) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.user_agent},
        timeout=httpx.Timeout(config.http_timeout_seconds),
        follow_redirects=True,
    )


def _looks_like_html(content_type: str) -> bool:
    return "text/html" in content_type or "application/xhtml" in content_type


def _extract_pdf_text(data: bytes) -> str:
    """Best-effort plain-text extraction from PDF bytes via pypdf.

    Returns ``""`` when the PDF yields no usable text (image-only/scanned PDFs,
    a parse error, or pypdf unavailable) so the caller degrades to title-only.
    pypdf is imported lazily so the rest of extraction works even if it is not
    installed in some environment.
    """
    if not data:
        return ""
    try:
        from pypdf import PdfReader  # lazy: optional dependency.
    except ImportError:  # pragma: no cover - pypdf is a runtime dependency.
        logger.warning("pypdf not available; skipping PDF text extraction")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any pypdf failure -> no text.
        logger.warning("PDF text extraction failed: %s", exc)
        return ""
    return "\n\n".join(p for p in pages if p.strip()).strip()


def _truncate(text: str, config: Config) -> str:
    """Collapse whitespace and cap to the configured character budget."""
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(cleaned) <= config.article_char_budget:
        return cleaned
    return cleaned[: config.article_char_budget].rstrip()


# --- GitHub README handling --------------------------------------------------


def _try_github_readme(url: str, client: httpx.Client, config: Config) -> Extracted | None:
    """If ``url`` is a GitHub repo, fetch its raw README; else ``None``."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo)

    # If the url points to a deep path (issues, blob, etc.), only treat the
    # bare repo root (or a tree/branch ref) as a README source.
    if len(parts) > 2 and parts[2] not in ("tree",):
        return None

    branches: tuple[str, ...] = _DEFAULT_BRANCHES
    if len(parts) >= 4 and parts[2] == "tree":
        branches = (parts[3], *_DEFAULT_BRANCHES)

    for branch in branches:
        for candidate in _README_CANDIDATES:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{candidate}"
            try:
                response = client.get(raw_url)
            except (httpx.HTTPError, httpx.TransportError):
                continue
            if response.status_code == 200 and response.text.strip():
                return Extracted(text=_truncate(response.text, config), kind="readme")
    return None
