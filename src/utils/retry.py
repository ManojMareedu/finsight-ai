"""Shared bounded retry with exponential backoff + jitter.

Lives here rather than in data_fetchers so the LLM path does not import a
data-source module just to retry.
"""

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)


def backoff_delay(attempt: int, base_delay: float = 0.5) -> float:
    """Exponential backoff with jitter for a 0-indexed retry attempt."""
    return base_delay * (2**attempt) + random.uniform(0, base_delay)


def retry_with_backoff(func, *, attempts: int = 3, base_delay: float = 0.5, is_retryable=None):
    """
    Call func() with exponential backoff + jitter. Retries only timeouts,
    connection errors, and 429/5xx responses; any other exception (incl.
    other 4xx) is raised immediately. Bounded to `attempts` tries total so a
    single call can't stall indefinitely against a synchronous API.
    """

    def _default_is_retryable(e: Exception) -> bool:
        if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        status = getattr(e, "status_code", None)
        if status is None:
            status = getattr(getattr(e, "response", None), "status_code", None)
        return status is not None and (status == 429 or status >= 500)

    check = is_retryable or _default_is_retryable
    for attempt in range(attempts):
        try:
            return func()
        except Exception as e:
            if attempt == attempts - 1 or not check(e):
                raise
            delay = backoff_delay(attempt, base_delay)
            logger.warning(
                f"Retrying after error ({e}); attempt {attempt + 1}/{attempts}, "
                f"sleeping {delay:.2f}s"
            )
            time.sleep(delay)
