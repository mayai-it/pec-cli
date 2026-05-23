"""Tests for IMAP retry behavior.

We mock `imaplib.IMAP4_SSL` so the tests are 100% offline. `time.sleep` is
patched too.
"""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest

from pec_cli.auth.credentials import Credentials
from pec_cli.imap.client import IMAPClient, IMAPError, _imap_is_retriable


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("pec_cli.retry.time.sleep"):
        yield


@pytest.fixture
def creds() -> Credentials:
    return Credentials(address="user@pec.it", provider="aruba", password="secret")


# ---------------------------------------------------------------------------
# Predicate behavior
# ---------------------------------------------------------------------------


def test_imap_is_retriable_classifies_oserror() -> None:
    assert _imap_is_retriable(OSError("connection reset"))
    assert _imap_is_retriable(imaplib.IMAP4.abort("server hung up"))


def test_imap_is_retriable_treats_transient_imap_errors_as_retriable() -> None:
    assert _imap_is_retriable(imaplib.IMAP4.error("[TRYAGAIN] please retry"))
    assert _imap_is_retriable(imaplib.IMAP4.error("[UNAVAILABLE] busy"))
    assert _imap_is_retriable(imaplib.IMAP4.error("[INUSE] mailbox locked"))


def test_imap_is_retriable_rejects_permanent_imap_errors() -> None:
    assert not _imap_is_retriable(
        imaplib.IMAP4.error("[AUTHENTICATIONFAILED] bad creds")
    )
    assert not _imap_is_retriable(imaplib.IMAP4.error("[NONEXISTENT] no such folder"))
    assert not _imap_is_retriable(ValueError("nothing to do with IMAP"))


# ---------------------------------------------------------------------------
# connect() retries
# ---------------------------------------------------------------------------


def test_imap_connect_retries_on_oserror_then_succeeds(creds: Credentials) -> None:
    attempts: list[int] = []

    def fake_ssl_ctor(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("network blip")
        return MagicMock()

    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", side_effect=fake_ssl_ctor):
        client = IMAPClient(creds)
        client.connect()

    assert len(attempts) == 3


def test_imap_connect_does_not_retry_on_authentication_failed(
    creds: Credentials,
) -> None:
    """Permanent failure — login with a wrong password must not loop."""
    fake_imap = MagicMock()
    fake_imap.login.side_effect = imaplib.IMAP4.error(
        "[AUTHENTICATIONFAILED] Invalid credentials"
    )

    with patch(
        "pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap
    ):
        client = IMAPClient(creds)
        with pytest.raises(IMAPError, match="IMAP login failed"):
            client.connect()

    # One ctor call, one login call — no retry.
    assert fake_imap.login.call_count == 1


def test_imap_connect_gives_up_after_max_retries(creds: Credentials) -> None:
    with patch(
        "pec_cli.imap.client.imaplib.IMAP4_SSL",
        side_effect=OSError("permanently broken"),
    ) as ssl_mock:
        client = IMAPClient(creds)
        with pytest.raises(IMAPError, match="could not reach"):
            client.connect()

    # Default max_retries=3 → 4 total attempts.
    assert ssl_mock.call_count == 4


# ---------------------------------------------------------------------------
# search() retries
# ---------------------------------------------------------------------------


def test_imap_search_retries_on_transient_error(creds: Credentials) -> None:
    fake_imap = MagicMock()
    fake_imap.uid.side_effect = [
        imaplib.IMAP4.error("[TRYAGAIN] busy"),
        ("OK", [b""]),
    ]
    client = IMAPClient(creds)
    client._imap = fake_imap  # bypass connect()

    uids = client.search()

    assert uids == []
    assert fake_imap.uid.call_count == 2
