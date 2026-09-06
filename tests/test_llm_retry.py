"""Network-free tests for src/utils/llm_client.structured_chat retry/backoff.

F7/J15: structured_chat retried 3x immediately with no backoff or jitter —
worse than not retrying against a 429. These fail if the backoff is removed
or the 3-attempt bound disappears."""

import logging
from unittest.mock import patch

import pytest
from pydantic import BaseModel

import src.utils.llm_client as llm_client


class _Schema(BaseModel):
    answer: str


def test_structured_chat_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def _fake_chat(messages, model=None, attempts=3):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient LLM failure")
        return '{"answer": "ok"}'

    monkeypatch.setattr(llm_client, "chat", _fake_chat)

    with patch("time.sleep") as mock_sleep:
        result = llm_client.structured_chat([{"role": "user", "content": "hi"}], _Schema)

    assert result.answer == "ok"
    assert calls["n"] == 3
    assert mock_sleep.call_count >= 1  # backoff actually applied between attempts


def test_structured_chat_gives_up_after_bound(monkeypatch):
    def _always_fails(messages, model=None):
        raise RuntimeError("still down")

    monkeypatch.setattr(llm_client, "chat", _always_fails)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(ValueError):
            llm_client.structured_chat([{"role": "user", "content": "hi"}], _Schema)

    # 3 attempts total -> 2 backoff sleeps (none after the final attempt)
    assert mock_sleep.call_count == 2


def test_structured_chat_does_not_stack_retries_with_chat(monkeypatch):
    # F-4: structured_chat's own 3-attempt loop must be the only retry layer.
    # If chat() also retried the raw API call internally, one structured_chat
    # request could make up to 3x3=9 calls instead of 3.
    calls = {"n": 0}

    class _RetryableError(Exception):
        status_code = 500

    class _FakeCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            raise _RetryableError("boom")

    class _FakeClient:
        chat = type("_C", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _FakeClient())

    with patch("time.sleep"):
        with pytest.raises(ValueError):
            llm_client.structured_chat([{"role": "user", "content": "hi"}], _Schema)

    assert calls["n"] == 3


def test_chat_records_the_provider_token_counts(monkeypatch, caplog):
    # Metering used to be a monkeypatch applied at import time from the API
    # module, which stopped counting anything the moment an importer changed
    # order. It now happens where the response is, and it carries real numbers.
    class _Usage:
        prompt_tokens = 1234
        completion_tokens = 56

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        usage = _Usage()

    client = llm_client.get_llm_client()
    monkeypatch.setattr(
        client.chat.completions, "create", lambda **kwargs: _Response(), raising=True
    )
    with caplog.at_level(logging.INFO, logger="llm.usage"):
        assert llm_client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert any(
        "prompt_tokens=1234" in r.getMessage() and "completion_tokens=56" in r.getMessage()
        for r in caplog.records
    )
