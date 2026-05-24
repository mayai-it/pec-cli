"""Tests for the MCP server safeguards around `pec_send`.

The MCP context object is opaque under the FastMCP runtime, so we build a
minimal stub instead of running the real server lifespan.

Why the `# type: ignore[arg-type]` lines below: the tool signature is
`Context[ServerSession, AppContext]` from FastMCP, but we pass our duck-typed
`_StubCtx` (same shape, no real session). Same reason for `imap=None` in the
AppContext — `pec_send` doesn't touch the IMAP connection, only `pec_list` /
`pec_get` / `pec_trace` do. The ignores are scoped to a specific mypy code so
they don't hide unrelated regressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from pec_cli.auth.credentials import Credentials
from pec_cli.daticert import DatiCert
from pec_cli.imap.client import IMAPError
from pec_cli.mcp_server import (
    _RATE_LIMIT_MAX_SENDS,
    AppContext,
    _reset_session_send_log,
    pec_auth_status,
    pec_get,
    pec_list,
    pec_list_folders,
    pec_mark_read,
    pec_mark_unread,
    pec_move,
    pec_search,
    pec_send,
    pec_trace,
)
from pec_cli.models.message import (
    Attachment,
    Message,
    MessageSummary,
)

# ---------------------------------------------------------------------------
# Fixtures and stubs
# ---------------------------------------------------------------------------


@dataclass
class _StubRequestContext:
    lifespan_context: AppContext


@dataclass
class _StubCtx:
    request_context: _StubRequestContext


@pytest.fixture(autouse=True)
def _reset_log() -> None:
    _reset_session_send_log()


@pytest.fixture
def fake_ctx() -> _StubCtx:
    creds = Credentials(address="user@pec.it", provider="aruba", password="secret")
    # imap=None: pec_send doesn't touch it; only pec_list/get/trace do.
    app = AppContext(creds=creds, imap=None)  # type: ignore[arg-type]
    return _StubCtx(request_context=_StubRequestContext(lifespan_context=app))


@pytest.fixture
def fake_ctx_with_imap() -> _StubCtx:
    """Variant whose AppContext carries a MagicMock IMAP client — used by
    the read-tools (pec_list / pec_get / pec_trace)."""
    creds = Credentials(address="user@pec.it", provider="aruba", password="secret")
    imap = MagicMock()
    app = AppContext(creds=creds, imap=imap)  # type: ignore[arg-type]
    return _StubCtx(request_context=_StubRequestContext(lifespan_context=app))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pec_send_requires_confirm_legal_send_true(fake_ctx: _StubCtx) -> None:
    with patch("pec_cli.mcp_server.send_pec") as send_mock:
        with pytest.raises(ToolError, match="confirm_legal_send"):
            pec_send(
                ctx=fake_ctx,  # type: ignore[arg-type]
                to=["dest@pec.it"],
                subject="x",
                body="hi",
            )
    send_mock.assert_not_called()


def test_pec_send_dry_run_returns_validated_without_smtp(fake_ctx: _StubCtx) -> None:
    with patch("pec_cli.mcp_server.send_pec") as send_mock:
        out = pec_send(
            ctx=fake_ctx,  # type: ignore[arg-type]
            to=["dest@pec.it"],
            subject="x",
            body="hi",
            dry_run=True,
        )
    send_mock.assert_not_called()
    assert out["dry_run"] is True
    assert out["validated"] is True
    assert out["to"] == ["dest@pec.it"]


def test_pec_send_dry_run_does_not_require_confirm_legal_send(
    fake_ctx: _StubCtx,
) -> None:
    """Dry-run is for validation/preview, so it shouldn't be gated by the
    legal-consent flag (which exists to guard the *actual* legal send)."""
    out = pec_send(
        ctx=fake_ctx,  # type: ignore[arg-type]
        to=["dest@pec.it"],
        subject="x",
        body="hi",
        dry_run=True,
        confirm_legal_send=False,
    )
    assert out["dry_run"] is True


def test_pec_send_validates_recipient_address(fake_ctx: _StubCtx) -> None:
    with pytest.raises(ToolError, match="invalid recipient"):
        pec_send(
            ctx=fake_ctx,  # type: ignore[arg-type]
            to=["not-an-email"],
            subject="x",
            body="hi",
            confirm_legal_send=True,
        )


def test_pec_send_rejects_empty_body(fake_ctx: _StubCtx) -> None:
    with pytest.raises(ToolError, match="empty"):
        pec_send(
            ctx=fake_ctx,  # type: ignore[arg-type]
            to=["dest@pec.it"],
            subject="x",
            body="   \n  ",
            confirm_legal_send=True,
        )


def test_pec_send_rate_limit_after_3_same_recipient(fake_ctx: _StubCtx) -> None:
    fake_result: dict[str, Any] = {
        "status": "sent",
        "to": ["dest@pec.it"],
        "cc": [],
        "subject": "x",
        "message_id": "<abc@mayai-pec-cli>",
        "attachments": [],
    }
    with patch("pec_cli.mcp_server.send_pec", return_value=fake_result):
        for _ in range(_RATE_LIMIT_MAX_SENDS):
            pec_send(
                ctx=fake_ctx,  # type: ignore[arg-type]
                to=["dest@pec.it"],
                subject="x",
                body="hi",
                confirm_legal_send=True,
            )
        # 4th call to the same recipient must trip the limiter.
        with pytest.raises(ToolError, match="Rate limit"):
            pec_send(
                ctx=fake_ctx,  # type: ignore[arg-type]
                to=["dest@pec.it"],
                subject="x",
                body="hi",
                confirm_legal_send=True,
            )


def test_pec_send_rate_limit_is_per_recipient(fake_ctx: _StubCtx) -> None:
    """Hitting the cap for one recipient must not block sends to a different one."""
    fake_result: dict[str, Any] = {
        "status": "sent",
        "to": [],
        "cc": [],
        "subject": "x",
        "message_id": "<x@mayai-pec-cli>",
        "attachments": [],
    }
    with patch("pec_cli.mcp_server.send_pec", return_value=fake_result):
        for _ in range(_RATE_LIMIT_MAX_SENDS):
            pec_send(
                ctx=fake_ctx,  # type: ignore[arg-type]
                to=["a@pec.it"],
                subject="x",
                body="hi",
                confirm_legal_send=True,
            )
        # Different recipient — should pass.
        out = pec_send(
            ctx=fake_ctx,  # type: ignore[arg-type]
            to=["b@pec.it"],
            subject="x",
            body="hi",
            confirm_legal_send=True,
        )
    assert out["status"] == "sent"


def test_pec_send_with_confirm_calls_smtp(fake_ctx: _StubCtx) -> None:
    fake_result: dict[str, Any] = {
        "status": "sent",
        "to": ["dest@pec.it"],
        "cc": [],
        "subject": "x",
        "message_id": "<abc@mayai-pec-cli>",
        "attachments": [],
    }
    with patch("pec_cli.mcp_server.send_pec", return_value=fake_result) as send_mock:
        out = pec_send(
            ctx=fake_ctx,  # type: ignore[arg-type]
            to=["dest@pec.it"],
            subject="x",
            body="hi",
            confirm_legal_send=True,
        )
    send_mock.assert_called_once()
    assert out["status"] == "sent"


# ---------------------------------------------------------------------------
# pec_list / pec_get / pec_trace / pec_auth_status — read tools
# ---------------------------------------------------------------------------


def test_pec_list_returns_dicts_from_summaries(fake_ctx_with_imap: _StubCtx) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search.return_value = ["1", "2"]
    imap.fetch_summaries.return_value = [
        MessageSummary(
            id="2",
            date="2026-03-21T10:00:00",
            from_addr="alice@pec.it",
            to_addrs=["bob@pec.it"],
            subject="hi",
            pec_type=None,
            unread=True,
            has_attachments=False,
        ),
    ]
    out = pec_list(ctx=fake_ctx_with_imap)  # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0]["from"] == "alice@pec.it"
    imap.select_folder.assert_called_once_with("INBOX")


def test_pec_list_propagates_imap_error_as_toolerror(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.select_folder.side_effect = IMAPError("no such mailbox")
    with pytest.raises(ToolError, match="no such mailbox"):
        pec_list(ctx=fake_ctx_with_imap)  # type: ignore[arg-type]


def test_pec_list_respects_limit_and_unread_flags(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search.return_value = [str(i) for i in range(50)]
    imap.fetch_summaries.return_value = []
    pec_list(ctx=fake_ctx_with_imap, unread_only=True, limit=5)  # type: ignore[arg-type]
    imap.search.assert_called_once_with(unread=True)
    # The slice passed to fetch_summaries must be the last 5 in newest-first order.
    fetched_uids = imap.fetch_summaries.call_args.args[0]
    assert len(fetched_uids) == 5
    assert fetched_uids[0] == "49"  # newest


def test_pec_get_returns_dict_for_existing_uid(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.fetch_message.return_value = Message(
        id="7",
        date="2026-03-21T10:00:00",
        from_addr="alice@pec.it",
        to_addrs=["bob@pec.it"],
        cc_addrs=[],
        subject="subj",
        pec_type=None,
        body_text="hi",
        body_html=None,
        attachments=[],
        daticert=None,
    )
    out = pec_get(ctx=fake_ctx_with_imap, message_id=7)  # type: ignore[arg-type]
    assert out["subject"] == "subj"
    imap.fetch_message.assert_called_once_with("7")


def test_pec_get_propagates_imap_error_as_toolerror(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.fetch_message.side_effect = IMAPError("not found")
    with pytest.raises(ToolError, match="not found"):
        pec_get(ctx=fake_ctx_with_imap, message_id=99)  # type: ignore[arg-type]


def test_pec_trace_empty_target_raises(fake_ctx_with_imap: _StubCtx) -> None:
    with pytest.raises(ToolError, match="empty"):
        pec_trace(ctx=fake_ctx_with_imap, message_id="<>")  # type: ignore[arg-type]


def test_pec_trace_returns_chain_for_matching_daticert(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search.return_value = ["1"]
    imap.fetch_summaries.return_value = [
        MessageSummary(
            id="1",
            date="2026-03-21T10:00:00",
            from_addr="provider@pec.it",
            to_addrs=["user@pec.it"],
            subject="ACCETTAZIONE",
            pec_type="accettazione",
            unread=True,
            has_attachments=True,
        )
    ]
    cert = DatiCert(
        tipo="accettazione",
        mittente="user@pec.it",
        destinatari=["dest@pec.it"],
        data="2026-03-21T10:00:00+00:00",
        identificativo="opec123.abc@pec.it",
        riferimento_message_id="orig-msg-id@example.com",
        oggetto="hello",
        errore="nessuno",
    )
    imap.fetch_message.return_value = Message(
        id="1",
        date="2026-03-21T10:00:00",
        from_addr="provider@pec.it",
        to_addrs=["user@pec.it"],
        cc_addrs=[],
        subject="ACCETTAZIONE",
        pec_type="accettazione",
        body_text="",
        body_html=None,
        attachments=[Attachment(filename="daticert.xml", content_type="application/xml", size=10)],
        daticert=cert,
    )
    chain = pec_trace(
        ctx=fake_ctx_with_imap,  # type: ignore[arg-type]
        message_id="<orig-msg-id@example.com>",  # angle brackets are stripped
    )
    assert len(chain) == 1
    assert chain[0]["tipo"] == "accettazione"
    # `errore == "nessuno"` collapses to None.
    assert chain[0]["errore"] is None


def test_pec_trace_skips_messages_without_daticert(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search.return_value = ["1"]
    imap.fetch_summaries.return_value = [
        MessageSummary(
            id="1",
            date="2026-03-21T10:00:00",
            from_addr="provider@pec.it",
            to_addrs=["user@pec.it"],
            subject="something",
            pec_type="accettazione",
            unread=True,
            has_attachments=False,
        )
    ]
    imap.fetch_message.return_value = Message(
        id="1",
        date="2026-03-21T10:00:00",
        from_addr="provider@pec.it",
        to_addrs=["user@pec.it"],
        cc_addrs=[],
        subject="something",
        pec_type="accettazione",
        body_text="",
        body_html=None,
        attachments=[],
        daticert=None,
    )
    assert pec_trace(
        ctx=fake_ctx_with_imap,  # type: ignore[arg-type]
        message_id="orig",
    ) == []


def test_pec_auth_status_returns_account_info(fake_ctx_with_imap: _StubCtx) -> None:
    out = pec_auth_status(ctx=fake_ctx_with_imap)  # type: ignore[arg-type]
    assert out["authenticated"] is True
    assert out["address"] == "user@pec.it"
    assert out["provider"] == "aruba"
    assert "imap" in out and "smtp" in out


# ---------------------------------------------------------------------------
# pec_search
# ---------------------------------------------------------------------------


def test_pec_search_returns_summaries(fake_ctx_with_imap: _StubCtx) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search_by_field.return_value = ["1"]
    imap.fetch_summaries.return_value = [
        MessageSummary(
            id="1",
            date="2026-04-01T10:00:00",
            from_addr="inps@pec.it",
            to_addrs=["user@pec.it"],
            subject="INPS",
            pec_type=None,
            unread=True,
            has_attachments=False,
        )
    ]
    out = pec_search(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap, query="INPS"
    )
    assert len(out) == 1
    assert out[0]["from"] == "inps@pec.it"


def test_pec_search_forwards_field_and_from_date(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search_by_field.return_value = []
    imap.fetch_summaries.return_value = []
    pec_search(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap,
        query="tax",
        field="from",
        from_date="2025-01-01",
    )
    imap.search_by_field.assert_called_once_with(
        "tax", field="from", since="2025-01-01"
    )


def test_pec_search_propagates_imap_error_as_toolerror(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.search_by_field.side_effect = IMAPError("bad query")
    with pytest.raises(ToolError, match="bad query"):
        pec_search(  # type: ignore[arg-type]
            ctx=fake_ctx_with_imap, query="x"
        )


# ---------------------------------------------------------------------------
# pec_list_folders
# ---------------------------------------------------------------------------


def test_pec_list_folders_without_counts(fake_ctx_with_imap: _StubCtx) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.list_folders.return_value = ["INBOX", "Sent"]
    out = pec_list_folders(ctx=fake_ctx_with_imap)  # type: ignore[arg-type]
    assert out == [{"name": "INBOX"}, {"name": "Sent"}]


def test_pec_list_folders_with_counts(fake_ctx_with_imap: _StubCtx) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.list_folders.return_value = ["INBOX", "Sent"]
    imap.folder_status.side_effect = [
        {"messages": 100, "unseen": 5},
        {"messages": 50, "unseen": 0},
    ]
    out = pec_list_folders(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap, include_counts=True
    )
    assert out[0] == {"name": "INBOX", "messages": 100, "unseen": 5}
    assert out[1] == {"name": "Sent", "messages": 50, "unseen": 0}


def test_pec_list_folders_handles_noselect_folder(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.list_folders.return_value = ["[PEC]"]
    imap.folder_status.side_effect = IMAPError("Noselect")
    out = pec_list_folders(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap, include_counts=True
    )
    assert out == [{"name": "[PEC]", "messages": None, "unseen": None}]


# ---------------------------------------------------------------------------
# pec_mark_read / pec_mark_unread
# ---------------------------------------------------------------------------


def test_pec_mark_read_invokes_set_seen_true(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    out = pec_mark_read(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap, message_id=42
    )
    imap.select_folder.assert_called_once_with("INBOX", readonly=False)
    imap.set_seen.assert_called_once_with("42", seen=True)
    assert out == {
        "message_id": 42,
        "folder": "INBOX",
        "action": "mark-read",
        "success": True,
    }


def test_pec_mark_unread_invokes_set_seen_false(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    out = pec_mark_unread(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap, message_id=42
    )
    imap.set_seen.assert_called_once_with("42", seen=False)
    assert out["action"] == "mark-unread"


def test_pec_mark_read_propagates_imap_error(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.set_seen.side_effect = IMAPError("STORE failed")
    with pytest.raises(ToolError, match="STORE failed"):
        pec_mark_read(  # type: ignore[arg-type]
            ctx=fake_ctx_with_imap, message_id=42
        )


# ---------------------------------------------------------------------------
# pec_move
# ---------------------------------------------------------------------------


def test_pec_move_validates_destination_exists(
    fake_ctx_with_imap: _StubCtx,
) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.folder_exists.return_value = False
    with pytest.raises(ToolError, match="not found"):
        pec_move(  # type: ignore[arg-type]
            ctx=fake_ctx_with_imap,
            message_id=42,
            to_folder="Nonexistent",
        )
    imap.move_message.assert_not_called()


def test_pec_move_returns_success_payload(fake_ctx_with_imap: _StubCtx) -> None:
    imap = fake_ctx_with_imap.request_context.lifespan_context.imap
    imap.folder_exists.return_value = True
    out = pec_move(  # type: ignore[arg-type]
        ctx=fake_ctx_with_imap,
        message_id=42,
        to_folder="Archive",
    )
    imap.select_folder.assert_called_once_with("INBOX", readonly=False)
    imap.move_message.assert_called_once_with("42", "Archive")
    assert out == {
        "message_id": 42,
        "from_folder": "INBOX",
        "to_folder": "Archive",
        "action": "move",
        "success": True,
    }
