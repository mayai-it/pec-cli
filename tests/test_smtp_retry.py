"""Tests for SMTP retry behavior.

The MIME message is built ONCE per `send_pec` call. Retries must reuse the
exact same `EmailMessage` instance (same Message-ID), otherwise PEC providers
won't deduplicate and the recipient sees N copies of one legal email.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from pec_cli.auth.credentials import Credentials
from pec_cli.smtp.sender import SMTPError, _smtp_is_retriable, send_pec


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


def test_smtp_predicate_retries_oserror_and_disconnect() -> None:
    assert _smtp_is_retriable(OSError("conn reset"))
    assert _smtp_is_retriable(smtplib.SMTPServerDisconnected("hung up"))


def test_smtp_predicate_retries_4xx_response() -> None:
    assert _smtp_is_retriable(smtplib.SMTPResponseException(421, b"try later"))
    assert _smtp_is_retriable(smtplib.SMTPResponseException(450, b"mailbox busy"))


def test_smtp_predicate_does_not_retry_5xx_response() -> None:
    assert not _smtp_is_retriable(smtplib.SMTPResponseException(550, b"no such user"))
    assert not _smtp_is_retriable(
        smtplib.SMTPResponseException(530, b"auth required")
    )


def test_smtp_predicate_does_not_retry_auth_failed() -> None:
    # SMTPAuthenticationError is an SMTPResponseException with 5xx code.
    exc = smtplib.SMTPAuthenticationError(535, b"bad password")
    assert not _smtp_is_retriable(exc)


# ---------------------------------------------------------------------------
# send_pec retry behavior
# ---------------------------------------------------------------------------


def _patch_smtp_ssl(behaviors: list):
    """Return a MagicMock-based SMTP_SSL context manager that runs `behaviors`
    in order across successive `with smtplib.SMTP_SSL(...)` blocks.

    Each entry in `behaviors` is either an exception (raised on send_message)
    or None (success).
    """
    contexts: list[MagicMock] = []
    for behavior in behaviors:
        ctx = MagicMock()
        if isinstance(behavior, BaseException):
            ctx.send_message.side_effect = behavior
        contexts.append(ctx)

    cm_factory = MagicMock()
    cms: list[MagicMock] = []
    for ctx in contexts:
        cm = MagicMock()
        cm.__enter__.return_value = ctx
        cm.__exit__.return_value = False
        cms.append(cm)
    cm_factory.side_effect = cms
    return cm_factory, contexts


def test_smtp_send_retries_on_disconnect_with_same_message_id(
    creds: Credentials,
) -> None:
    cm_factory, ctxs = _patch_smtp_ssl(
        [smtplib.SMTPServerDisconnected("eof"), None]
    )
    with patch("pec_cli.smtp.sender.smtplib.SMTP_SSL", cm_factory):
        result = send_pec(
            creds, to=["dest@pec.it"], subject="hi", body="ciao"
        )

    # Two SMTP sessions opened (one failed, one succeeded).
    assert cm_factory.call_count == 2
    # Both attempts received a send_message; both should have the SAME message
    # object — guarantees Message-ID preservation.
    sent_msgs_1 = ctxs[0].send_message.call_args.args[0]
    sent_msgs_2 = ctxs[1].send_message.call_args.args[0]
    assert sent_msgs_1 is sent_msgs_2
    assert sent_msgs_1["Message-ID"] == sent_msgs_2["Message-ID"]
    assert result["message_id"] == sent_msgs_1["Message-ID"]


def test_smtp_send_retries_on_4xx_response(creds: Credentials) -> None:
    cm_factory, _ = _patch_smtp_ssl(
        [smtplib.SMTPResponseException(421, b"try later"), None]
    )
    with patch("pec_cli.smtp.sender.smtplib.SMTP_SSL", cm_factory):
        send_pec(creds, to=["dest@pec.it"], subject="hi", body="ciao")

    assert cm_factory.call_count == 2


def test_smtp_send_does_not_retry_on_5xx_response(creds: Credentials) -> None:
    cm_factory, _ = _patch_smtp_ssl(
        [smtplib.SMTPResponseException(550, b"no such user")]
    )
    with patch("pec_cli.smtp.sender.smtplib.SMTP_SSL", cm_factory):
        with pytest.raises(SMTPError, match="SMTP error"):
            send_pec(creds, to=["dest@pec.it"], subject="hi", body="ciao")

    # Single attempt, no retry.
    assert cm_factory.call_count == 1


def test_smtp_send_preserves_message_id_across_two_retries(
    creds: Credentials,
) -> None:
    """The key idempotency guarantee — verify Message-ID stability through
    multiple retries."""
    cm_factory, ctxs = _patch_smtp_ssl(
        [
            OSError("blip 1"),
            smtplib.SMTPServerDisconnected("blip 2"),
            None,
        ]
    )
    with patch("pec_cli.smtp.sender.smtplib.SMTP_SSL", cm_factory):
        result = send_pec(
            creds, to=["dest@pec.it"], subject="hello", body="testbody"
        )

    assert cm_factory.call_count == 3
    msg_ids = [ctx.send_message.call_args.args[0]["Message-ID"] for ctx in ctxs]
    # All three attempts must carry the IDENTICAL Message-ID.
    assert msg_ids[0] == msg_ids[1] == msg_ids[2]
    # And the same value is reported back to the caller.
    assert result["message_id"] == msg_ids[0]


def test_smtp_send_gives_up_after_max_retries(creds: Credentials) -> None:
    cm_factory, _ = _patch_smtp_ssl([OSError("permanently broken")] * 4)
    with patch("pec_cli.smtp.sender.smtplib.SMTP_SSL", cm_factory):
        with pytest.raises(SMTPError, match="could not reach"):
            send_pec(creds, to=["dest@pec.it"], subject="hi", body="ciao")

    # Default max_retries=3 → 4 attempts total.
    assert cm_factory.call_count == 4
