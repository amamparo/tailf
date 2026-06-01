"""Tests for extraction's network-free paths: the og:description fallback and
the resilient PDF helper.

Extraction does NOT judge content quality (no char-count thresholds) — it hands
over whatever text exists and the model decides gist-vs-SKIP. So these tests
cover *what text gets handed over*, not whether it's "good enough".
"""

from __future__ import annotations

from tailf.config import Config
from tailf.extract import _extract_pdf_text, extract_article_from_html


def test_body_is_preferred_when_present() -> None:
    body = (
        "<p>Researchers measured energy use per inference token across model "
        "sizes and found batching cut per-token cost by roughly forty percent.</p>"
    )
    html = (
        '<html><head><meta property="og:description" content="a short teaser">'
        f"</head><body><article>{body}</article></body></html>"
    )
    extracted = extract_article_from_html(html, "https://example.com/a", Config())
    assert extracted is not None
    assert "inference token" in extracted.text  # the body, not the description


def test_description_fallback_when_body_unscrapable() -> None:
    # No real body (paywall / SPA shell) but an og:description present -> gist
    # from the description rather than hiding the post.
    desc = "A detailed look at why technical interviews are on their last legs."
    html = (
        f'<html><head><meta property="og:description" content="{desc}">'
        "</head><body></body></html>"
    )
    extracted = extract_article_from_html(html, "https://example.com/a", Config())
    assert extracted is not None
    assert extracted.text == desc


def test_none_when_no_text_anywhere() -> None:
    html = "<html><head></head><body></body></html>"
    assert extract_article_from_html(html, "https://example.com/a", Config()) is None


def test_extract_pdf_text_empty_bytes_returns_empty() -> None:
    assert _extract_pdf_text(b"") == ""


def test_extract_pdf_text_garbage_bytes_returns_empty() -> None:
    # Not a real PDF -> pypdf raises (or is absent); either way we get "" and
    # never propagate an exception.
    assert _extract_pdf_text(b"%PDF-1.4 not actually a valid pdf body") == ""
