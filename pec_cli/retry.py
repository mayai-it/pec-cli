"""Retry helpers for IMAP/SMTP operations against transient failures.

Two entry points:

- `with_retry(op, retriable_exceptions=...)` — class-based predicate. Use
  when "what is transient" is exactly an exception type tuple.
- `with_retry_predicate(op, is_retriable=...)` — callable predicate. Use when
  the decision depends on attributes of the exception (e.g. SMTP 4xx vs 5xx
  response codes).

Backoff is exponential with a hard cap so a flaky upstream can't pin us in
sleep() indefinitely. The retry logger is `pec.retry`; wire it up in `main.py`
under `--verbose` to surface retry events on stderr.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger("pec.retry")

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff: 1s, 2s, 4s, ..., capped at BACKOFF_CAP_SECONDS."""
    raw: float = BACKOFF_BASE_SECONDS * (2**attempt)
    return min(raw, BACKOFF_CAP_SECONDS)


def with_retry(
    operation: Callable[[], T],
    *,
    retriable_exceptions: tuple[type[BaseException], ...],
    max_retries: int = DEFAULT_MAX_RETRIES,
    operation_name: str = "operation",
) -> T:
    """Execute `operation`, retrying on `retriable_exceptions`.

    Non-retriable exceptions propagate immediately. After `max_retries`
    retries (i.e. `max_retries + 1` total attempts) the last exception is
    re-raised unchanged so callers see the real failure.
    """
    return with_retry_predicate(
        operation,
        is_retriable=lambda exc: isinstance(exc, retriable_exceptions),
        max_retries=max_retries,
        operation_name=operation_name,
    )


def with_retry_predicate(
    operation: Callable[[], T],
    *,
    is_retriable: Callable[[BaseException], bool],
    max_retries: int = DEFAULT_MAX_RETRIES,
    operation_name: str = "operation",
) -> T:
    """Like `with_retry` but uses a predicate to decide if an exception is
    transient. Lets callers express conditions like "4xx but not 5xx"."""
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except BaseException as exc:
            if not is_retriable(exc):
                raise
            last_exc = exc
            if attempt == max_retries:
                raise
            delay = _backoff_delay(attempt)
            log.warning(
                "%s failed (attempt %d/%d): %s — retry in %.1fs",
                operation_name,
                attempt + 1,
                max_retries + 1,
                exc,
                delay,
            )
            time.sleep(delay)
    # Unreachable: the loop either returns or re-raises. `assert` placates the
    # type checker without a `# type: ignore`.
    assert last_exc is not None
    raise last_exc
