"""Network-free tests for src/utils/retry.retry_with_backoff.

F7/J15: the helper wrapping every EDGAR and LLM call had no direct coverage —
reverting the wrapper left the whole suite green. time.sleep is patched, so
these never actually sleep.
"""

from unittest.mock import patch

import pytest
import requests

from src.utils.retry import retry_with_backoff


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(response=resp)


def test_retries_transient_error_then_succeeds():
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.Timeout("timed out")
        return "ok"

    with patch("time.sleep") as mock_sleep:
        assert retry_with_backoff(_flaky) == "ok"
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2


def test_bounded_to_attempts_and_reraises():
    calls = {"n": 0}

    def _always_429():
        calls["n"] += 1
        raise _http_error(429)

    with patch("time.sleep"):
        with pytest.raises(requests.exceptions.HTTPError):
            retry_with_backoff(_always_429)
    assert calls["n"] == 3  # bounded, not unbounded


def test_does_not_retry_client_errors():
    calls = {"n": 0}

    def _not_found():
        calls["n"] += 1
        raise _http_error(404)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(requests.exceptions.HTTPError):
            retry_with_backoff(_not_found)
    assert calls["n"] == 1
    assert mock_sleep.call_count == 0
