"""SMTP-over-SSL sender for PEC messages.

Italian PEC providers all expose SMTPS on port 465 (implicit TLS), not STARTTLS
on 587 — so we use smtplib.SMTP_SSL directly.
"""

from __future__ import annotations

import hashlib
import mimetypes
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

from pec_cli.auth import Credentials
from pec_cli.retry import with_retry_predicate


class SMTPError(Exception):
    """Raised on SMTP login or send failures."""


def _smtp_is_retriable(exc: BaseException) -> bool:
    """Transient SMTP conditions worth retrying.

    Order matters: `smtplib.SMTPException` inherits from `OSError` in the
    stdlib, so we must check the SMTP-specific subclasses FIRST. Otherwise
    a 5xx `SMTPResponseException` (and `SMTPAuthenticationError`, which is
    a 5xx subclass) would be incorrectly classified as a transient
    network error.

    - `SMTPResponseException` with a 4xx code is "try again later" per
      RFC 5321; 5xx codes are permanent and must NOT trigger a retry.
    - `SMTPAuthenticationError` is a `SMTPResponseException` subclass with
      5xx codes, so the 4xx check filters it out automatically.
    - `SMTPServerDisconnected` is treated as transient — the server hung
      up mid-session, the next attempt may land on a healthy worker.
    - Plain `OSError` covers socket.timeout, ConnectionResetError, etc.
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True
    # Bare OSError check goes LAST so the SMTP subclasses above run first.
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


def _build_message_id(
    *,
    from_addr: str,
    to: list[str],
    cc: list[str] | None,
    subject: str,
    body: str,
    minute_bucket: int | None = None,
) -> str:
    """Deterministic Message-ID for idempotency.

    Same (from, to, cc, subject, body) sent within the same UTC minute yields
    the same Message-ID — so an accidental retry doesn't show up as two
    distinct legal communications. Distinct content or a later minute produce
    a distinct ID, which is the intended semantic for a deliberate resend.
    """
    bucket = minute_bucket if minute_bucket is not None else int(time.time() // 60)
    h = hashlib.sha256()
    h.update(from_addr.encode("utf-8"))
    for addr in sorted(to):
        h.update(b"|to|")
        h.update(addr.encode("utf-8"))
    for addr in sorted(cc or []):
        h.update(b"|cc|")
        h.update(addr.encode("utf-8"))
    h.update(b"|s|")
    h.update(subject.encode("utf-8"))
    h.update(b"|b|")
    h.update(body.encode("utf-8", errors="replace"))
    h.update(f"|t|{bucket}".encode())
    return f"<{h.hexdigest()[:32]}@mayai-pec-cli>"


def send_pec(
    creds: Credentials,
    *,
    to: list[str],
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
    cc: list[str] | None = None,
    verbose: bool = False,
) -> dict:
    """Send a PEC message. Returns a small dict describing the result."""
    msg = EmailMessage()
    msg["From"] = creds.address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    message_id = _build_message_id(
        from_addr=creds.address, to=to, cc=cc, subject=subject, body=body
    )
    msg["Message-ID"] = message_id
    msg.set_content(body)

    for path in attachments or []:
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        data = Path(path).read_bytes()
        msg.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=Path(path).name
        )

    pc = creds.provider_config
    recipients = list(to) + list(cc or [])

    t0 = time.monotonic()

    # CRITICAL: `msg` (incl. its deterministic Message-ID) is built ONCE
    # above and reused across retries. Reconstructing the MIME inside the
    # retry callable would shift the minute-bucket and mint a new
    # Message-ID, defeating provider-side deduplication and producing
    # multiple legally-binding PECs from one logical send.
    def _do_send() -> None:
        with smtplib.SMTP_SSL(
            pc.smtp_host, pc.smtp_port, context=ssl.create_default_context()
        ) as smtp:
            if verbose:
                smtp.set_debuglevel(1)
            smtp.login(creds.address, creds.password)
            smtp.send_message(msg, from_addr=creds.address, to_addrs=recipients)

    try:
        with_retry_predicate(
            _do_send,
            is_retriable=_smtp_is_retriable,
            operation_name=f"SMTP send to {pc.smtp_host}",
        )
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPError(f"SMTP authentication failed: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise SMTPError(f"SMTP error: {exc}") from exc
    except OSError as exc:
        raise SMTPError(f"could not reach {pc.smtp_host}:{pc.smtp_port}: {exc}") from exc

    if verbose:
        elapsed = (time.monotonic() - t0) * 1000
        sys.stderr.write(f"smtp: sent via {pc.smtp_host}:{pc.smtp_port} ({elapsed:.0f}ms)\n")
        sys.stderr.write(f"smtp: message-id: {message_id}\n")

    return {
        "status": "sent",
        "to": to,
        "cc": cc or [],
        "subject": subject,
        "message_id": message_id,
        "attachments": [Path(p).name for p in (attachments or [])],
    }
