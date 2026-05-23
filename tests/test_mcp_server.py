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
from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from pec_cli.auth.credentials import Credentials
from pec_cli.mcp_server import (
    _RATE_LIMIT_MAX_SENDS,
    AppContext,
    _reset_session_send_log,
    pec_send,
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
