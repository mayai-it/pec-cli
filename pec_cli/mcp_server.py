"""MCP server exposing pec-cli as native tools for AI agents.

Runs over stdio. An IMAP connection is opened once at startup and shared by all
tools via the FastMCP lifespan context. Credentials are loaded from the same
on-disk store the CLI uses (`pec auth login`); the server refuses to start if
no credentials are present.

CRITICAL: never write to stdout — stdio transport reserves it for the MCP
protocol. All diagnostics go to stderr.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession

from pec_cli.auth import Credentials, load_credentials
from pec_cli.imap import IMAPClient, IMAPError
from pec_cli.smtp import SMTPError, send_pec

# In-process send log used to rate-limit `pec_send` within an MCP session.
# Each entry: {"to": str, "sent_at": float (monotonic seconds)}.
# Module-level on purpose — one MCP session = one process, so this is the
# right scope. Reset for tests via _reset_session_send_log().
_SESSION_SEND_LOG: list[dict[str, Any]] = []
_RATE_LIMIT_WINDOW_SEC = 300  # 5 minutes
_RATE_LIMIT_MAX_SENDS = 3


def _reset_session_send_log() -> None:
    """Test hook — clears the in-process send log."""
    _SESSION_SEND_LOG.clear()


_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class AppContext:
    creds: Credentials
    imap: IMAPClient


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    try:
        creds = load_credentials()
    except RuntimeError as exc:
        sys.stderr.write(f"pec-mcp: credentials unreadable: {exc}\n")
        raise SystemExit(2) from exc
    if creds is None:
        sys.stderr.write("pec-mcp: not authenticated — run `pec auth login` first\n")
        raise SystemExit(2)

    imap = IMAPClient(creds)
    try:
        imap.connect()
    except IMAPError as exc:
        sys.stderr.write(f"pec-mcp: IMAP connection failed: {exc}\n")
        raise SystemExit(2) from exc

    sys.stderr.write(f"pec-mcp: connected as {creds.address} ({creds.provider})\n")
    try:
        yield AppContext(creds=creds, imap=imap)
    finally:
        imap.close()


mcp = FastMCP("pec-cli", lifespan=app_lifespan)


def _app(ctx: Context[ServerSession, AppContext]) -> AppContext:
    return ctx.request_context.lifespan_context


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def pec_list(
    ctx: Context[ServerSession, AppContext],
    folder: str = "INBOX",
    unread_only: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List PEC messages, newest first.

    Args:
        folder: Folder alias (`inbox`, `sent`) or raw IMAP folder name.
        unread_only: If true, return only unread messages.
        limit: Maximum number of messages to return.

    Returns:
        One dict per message with keys: id, date, from, to, subject, pec_type,
        unread, has_attachments.
    """
    imap = _app(ctx).imap
    try:
        imap.select_folder(folder)
        uids = imap.search(unread=unread_only)
        uids = uids[-limit:][::-1] if limit else uids[::-1]
        summaries = imap.fetch_summaries(uids)
    except IMAPError as exc:
        raise ToolError(str(exc)) from exc

    return [s.to_dict() for s in summaries]


@mcp.tool()
def pec_get(
    ctx: Context[ServerSession, AppContext],
    message_id: int,
    include_cert: bool = False,
    folder: str = "INBOX",
) -> dict[str, Any]:
    """Fetch the full body of a PEC message by IMAP UID.

    Args:
        message_id: IMAP UID of the message (as returned by `pec_list`).
        include_cert: If true, include the parsed `daticert.xml` fields under
            `pec_cert` (tipo, mittente, destinatari, identificativo,
            riferimento_message_id, data, ...).
        folder: Folder alias or raw IMAP folder name to look in.

    Returns:
        Dict with the message body, headers, attachment list, and (optionally)
        the parsed PEC certification.
    """
    imap = _app(ctx).imap
    try:
        imap.select_folder(folder)
        message = imap.fetch_message(str(message_id))
    except IMAPError as exc:
        raise ToolError(str(exc)) from exc

    return message.to_dict(include_cert=include_cert)


@mcp.tool()
def pec_send(
    ctx: Context[ServerSession, AppContext],
    to: list[str],
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    confirm_legal_send: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send a PEC (legally binding certified email).

    ⚠️ This sends a real legal-equivalent email with cost and legal value
    (equivalent to a registered letter under Italian law). The agent MUST
    obtain explicit user consent before calling, and set
    `confirm_legal_send=True`. Rate-limited to 3 PECs per recipient per 5
    minutes within a single MCP session.

    For testing without sending, use `dry_run=True` — the message is
    validated (recipient format, attachment existence, non-empty body) and
    the result is returned with `dry_run: true` but no SMTP call is made.

    Args:
        to: Recipient PEC addresses.
        subject: Subject line.
        body: Plain-text body.
        attachments: Optional list of file paths to attach.
        confirm_legal_send: Must be True to actually send. Required even
            in dry_run mode is OFF — the agent must acknowledge legal scope.
        dry_run: If True, validate and return without contacting SMTP.

    Returns:
        Dict with `status`, `to`, `cc`, `subject`, `message_id`, and
        `attachments` (filenames). In dry_run mode also includes
        `dry_run: true` and `validated: true`.
    """
    # ----- Validation (runs even in dry_run) ---------------------------
    if not to:
        raise ToolError("`to` must contain at least one recipient address")
    for addr in to:
        if not _ADDR_RE.match(addr):
            raise ToolError(f"invalid recipient address: {addr!r}")
    if not body.strip():
        raise ToolError("`body` must not be empty")

    paths: list[Path] = []
    for raw in attachments or []:
        p = Path(raw).expanduser()
        if not p.exists() or not p.is_file():
            raise ToolError(f"attachment not found: {raw}")
        paths.append(p)

    # ----- Dry run shortcut --------------------------------------------
    if dry_run:
        return {
            "status": "dry-run",
            "dry_run": True,
            "validated": True,
            "to": list(to),
            "subject": subject,
            "attachments": [p.name for p in paths],
        }

    # ----- Explicit legal-consent gate ---------------------------------
    if not confirm_legal_send:
        raise ToolError(
            "PEC has the legal value of a registered letter (raccomandata). "
            "Set confirm_legal_send=True to proceed. This must be confirmed "
            "by the user, not the agent autonomously."
        )

    # ----- Rate limit (per-recipient, per-session) ---------------------
    now = time.monotonic()
    # Drop expired entries opportunistically.
    _SESSION_SEND_LOG[:] = [
        s for s in _SESSION_SEND_LOG if now - s["sent_at"] < _RATE_LIMIT_WINDOW_SEC
    ]
    for addr in to:
        recent = [s for s in _SESSION_SEND_LOG if s["to"] == addr]
        if len(recent) >= _RATE_LIMIT_MAX_SENDS:
            raise ToolError(
                f"Rate limit: {_RATE_LIMIT_MAX_SENDS} PECs to {addr} in the "
                f"last {_RATE_LIMIT_WINDOW_SEC // 60} minutes. Aborting to "
                "prevent accidental duplicates."
            )

    creds = _app(ctx).creds
    try:
        result = send_pec(
            creds,
            to=list(to),
            subject=subject,
            body=body,
            attachments=paths or None,
        )
    except SMTPError as exc:
        raise ToolError(str(exc)) from exc

    for addr in to:
        _SESSION_SEND_LOG.append({"to": addr, "sent_at": now})

    return result


@mcp.tool()
def pec_trace(
    ctx: Context[ServerSession, AppContext],
    message_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Trace the receipt chain (accettazione, presa-in-carico, consegna, ...)
    for a sent PEC, identified by its original RFC-822 Message-ID.

    Args:
        message_id: The Message-ID of the original sent mail. Angle brackets
            are stripped automatically.
        limit: Max recent PEC receipts in INBOX to scan.

    Returns:
        List of receipt events sorted by date, each with id, tipo, data,
        identificativo, mittente, destinatari, and (when present) errore.
    """
    target = message_id.strip().lstrip("<").rstrip(">").strip()
    if not target:
        raise ToolError("message_id is empty")

    imap = _app(ctx).imap
    try:
        imap.select_folder("INBOX")
        uids = imap.search()
        uids = uids[-limit:] if limit else uids
        summaries = imap.fetch_summaries(uids)
        pec_uids = [s.id for s in summaries if s.pec_type]

        chain: list[dict[str, Any]] = []
        for uid in pec_uids:
            msg = imap.fetch_message(uid)
            if msg.daticert is None:
                continue
            if msg.daticert.riferimento_message_id != target:
                continue
            err = msg.daticert.errore
            chain.append({
                "id": msg.id,
                "tipo": msg.daticert.tipo,
                "data": msg.daticert.data,
                "identificativo": msg.daticert.identificativo,
                "mittente": msg.daticert.mittente,
                "destinatari": msg.daticert.destinatari,
                "errore": err if err and err != "nessuno" else None,
            })
    except IMAPError as exc:
        raise ToolError(str(exc)) from exc

    chain.sort(key=lambda r: r.get("data") or "")
    return chain


@mcp.tool()
def pec_auth_status(ctx: Context[ServerSession, AppContext]) -> dict[str, Any]:
    """Report which PEC account this server is bound to.

    Returns:
        Dict with `authenticated`, `address`, and `provider`. If this tool
        returns, the server is authenticated by construction — it would have
        refused to start otherwise.
    """
    creds = _app(ctx).creds
    pc = creds.provider_config
    return {
        "authenticated": True,
        "address": creds.address,
        "provider": creds.provider,
        "imap": f"{pc.imap_host}:{pc.imap_port}",
        "smtp": f"{pc.smtp_host}:{pc.smtp_port}",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
