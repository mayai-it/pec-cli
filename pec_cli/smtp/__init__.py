"""SMTP client for sending PEC messages."""

from pec_cli.smtp.sender import SMTPError, send_pec

__all__ = ["SMTPError", "send_pec"]
