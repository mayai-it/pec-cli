"""SMTP-over-SSL sender for PEC messages.

Italian PEC providers all expose SMTPS on port 465 (implicit TLS), not STARTTLS
on 587 — so we use smtplib.SMTP_SSL directly.
"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

from pec_cli.auth import Credentials


class SMTPError(Exception):
    """Raised on SMTP login or send failures."""


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
    try:
        with smtplib.SMTP_SSL(
            pc.smtp_host, pc.smtp_port, context=ssl.create_default_context()
        ) as smtp:
            if verbose:
                smtp.set_debuglevel(1)
            smtp.login(creds.address, creds.password)
            smtp.send_message(msg, from_addr=creds.address, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPError(f"SMTP authentication failed: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise SMTPError(f"SMTP error: {exc}") from exc
    except OSError as exc:
        raise SMTPError(f"could not reach {pc.smtp_host}:{pc.smtp_port}: {exc}") from exc

    if verbose:
        elapsed = (time.monotonic() - t0) * 1000
        sys.stderr.write(f"smtp: sent via {pc.smtp_host}:{pc.smtp_port} ({elapsed:.0f}ms)\n")

    return {
        "status": "sent",
        "to": to,
        "cc": cc or [],
        "subject": subject,
        "attachments": [Path(p).name for p in (attachments or [])],
    }
