"""Tests for the generic retry helper.

`time.sleep` is patched everywhere — these tests verify control flow and
backoff timing, not real wall-clock waits.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pec_cli.retry import (
    BACKOFF_CAP_SECONDS,
    _backoff_delay,
    with_retry,
    with_retry_predicate,
)


@pytest.fixture
def fake_sleep():
    """Capture all `time.sleep` calls made from inside pec_cli.retry."""
    with patch("pec_cli.retry.time.sleep") as m:
        yield m


# ---------------------------------------------------------------------------
# with_retry — class-based predicate
# ---------------------------------------------------------------------------


def test_with_retry_succeeds_on_first_attempt(fake_sleep) -> None:
    calls = []

    def op() -> str:
        calls.append(1)
        return "ok"

    result = with_retry(op, retriable_exceptions=(OSError,))
    assert result == "ok"
    assert len(calls) == 1
    fake_sleep.assert_not_called()


def test_with_retry_succeeds_on_third_attempt(fake_sleep) -> None:
    calls = []

    def op() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise OSError("blip")
        return "ok"

    result = with_retry(op, retriable_exceptions=(OSError,))
    assert result == "ok"
    assert len(calls) == 3
    # Two retries → two sleeps (between attempt 1→2 and 2→3).
    assert fake_sleep.call_count == 2


def test_with_retry_raises_after_max_retries(fake_sleep) -> None:
    def op() -> None:
        raise OSError("always")

    with pytest.raises(OSError, match="always"):
        with_retry(op, retriable_exceptions=(OSError,), max_retries=3)
    # max_retries=3 means 4 attempts total, 3 sleeps between them.
    assert fake_sleep.call_count == 3


def test_with_retry_does_not_retry_on_non_retriable(fake_sleep) -> None:
    calls = []

    def op() -> None:
        calls.append(1)
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        with_retry(op, retriable_exceptions=(OSError,))
    assert len(calls) == 1
    fake_sleep.assert_not_called()


def test_with_retry_backoff_grows_exponentially(fake_sleep) -> None:
    def op() -> None:
        raise OSError("blip")

    with pytest.raises(OSError):
        with_retry(op, retriable_exceptions=(OSError,), max_retries=4)

    delays = [call.args[0] for call in fake_sleep.call_args_list]
    # 4 retries → 4 sleeps. Sequence: 1, 2, 4, 8 (all below cap).
    assert delays == [1.0, 2.0, 4.0, 8.0]


def test_with_retry_caps_at_30s(fake_sleep) -> None:
    # _backoff_delay(5) would be 32 without cap; _backoff_delay(6) would be 64.
    assert _backoff_delay(5) == BACKOFF_CAP_SECONDS
    assert _backoff_delay(6) == BACKOFF_CAP_SECONDS
    assert _backoff_delay(20) == BACKOFF_CAP_SECONDS


# ---------------------------------------------------------------------------
# with_retry_predicate
# ---------------------------------------------------------------------------


def test_with_retry_predicate_retries_when_true(fake_sleep) -> None:
    calls = []

    def op() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("temporary code 42")
        return "ok"

    result = with_retry_predicate(
        op,
        is_retriable=lambda exc: "42" in str(exc),
    )
    assert result == "ok"
    assert len(calls) == 2


def test_with_retry_predicate_skips_when_false(fake_sleep) -> None:
    calls = []

    def op() -> None:
        calls.append(1)
        raise RuntimeError("code 99 — permanent")

    with pytest.raises(RuntimeError):
        with_retry_predicate(
            op,
            is_retriable=lambda exc: "42" in str(exc),
        )
    assert len(calls) == 1
    fake_sleep.assert_not_called()
