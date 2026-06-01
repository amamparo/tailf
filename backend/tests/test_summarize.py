"""Tests for summarize: title-only reachability, kind stamping, failure -> None.

Uses a fake Anthropic-shaped client so nothing touches the network.
"""

from __future__ import annotations

import anthropic
from tailf.config import Config
from tailf.extract import Extracted
from tailf.summarize import summarize


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, owner: FakeClient) -> None:
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.raise_exc is not None:
            raise self._owner.raise_exc
        return _Response(self._owner.reply)


class FakeClient:
    """Minimal stand-in for anthropic.Anthropic used by summarize()."""

    def __init__(self, reply: str = "A neutral gist.", raise_exc: Exception | None = None) -> None:
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.messages = _Messages(self)


def test_no_content_produces_no_gist() -> None:
    # No extractable content -> no gist, and the API is NOT called. We never
    # summarize the title alone (a title restatement adds nothing).
    client = FakeClient(reply="should never be used")
    gist = summarize(client, "A video codec demo", extracted=None, config=Config())

    assert gist is None
    assert len(client.calls) == 0


def test_empty_extracted_text_produces_no_gist() -> None:
    client = FakeClient()
    gist = summarize(
        client, "Some title", extracted=Extracted(text="   ", kind="pdf"), config=Config()
    )
    assert gist is None
    assert len(client.calls) == 0


def test_model_skip_reply_returns_none() -> None:
    # The model declines a junk/boilerplate excerpt by replying SKIP -> no gist.
    client = FakeClient(reply="SKIP")
    gist = summarize(
        client,
        "Title",
        extracted=Extracted(text="You need to accept cookies to continue.", kind="article"),
        config=Config(),
    )
    assert gist is None


def test_pdf_content_stamps_pdf_kind() -> None:
    client = FakeClient()
    gist = summarize(
        client,
        "A paper (PDF)",
        extracted=Extracted(text="The paper measures energy per token.", kind="pdf"),
        config=Config(),
    )
    assert gist is not None
    assert gist.kind == "pdf"


def test_article_content_stamps_article_kind() -> None:
    client = FakeClient()
    gist = summarize(
        client,
        "An article",
        extracted=Extracted(text="Body text here.", kind="article"),
        config=Config(),
    )
    assert gist is not None
    assert gist.kind == "article"


def test_api_failure_returns_none() -> None:
    err = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    client = FakeClient(raise_exc=err)
    gist = summarize(
        client,
        "Title",
        extracted=Extracted(text="Body text.", kind="article"),
        config=Config(),
    )
    assert gist is None
