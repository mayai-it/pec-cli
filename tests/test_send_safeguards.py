"""Tests for the safety gates around `pec send` (CLI) and `pec_send` (MCP).

These guard the most consequential operation in the package: actually sending
a PEC, which has the legal value of a registered letter under Italian law.
We do NOT exercise SMTP here — `send_pec` is patched out — so failures
indicate a regression in our gating logic, not in the network layer.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from pec_cli.auth.credentials import Credentials
from pec_cli.main import cli
from pec_cli.smtp.sender import _build_message_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def creds() -> Credentials:
    return Credentials(address="user@pec.it", provider="aruba", password="secret")


@pytest.fixture
def mock_load_credentials(creds: Credentials):
    """Patch every load_credentials reference used by the CLI flow."""
    with (
        patch("pec_cli.main.load_credentials", return_value=creds),
        patch("pec_cli.auth.credentials.load_credentials", return_value=creds),
    ):
        yield


# ---------------------------------------------------------------------------
# CLI: pec send safeguards
# ---------------------------------------------------------------------------


def test_pec_send_dry_run_validates_but_does_not_call_smtp(
    mock_load_credentials,
) -> None:
    runner = CliRunner()
    with patch("pec_cli.main.send_pec") as send_mock:
        result = runner.invoke(
            cli,
            [
                "send",
                "--to",
                "dest@pec.it",
                "--subject",
                "x",
                "--body",
                "hi",
                "--dry-run",
            ],
        )
    assert result.exit_code == 0, result.output
    send_mock.assert_not_called()
    assert "dry-run" in result.output


def test_pec_send_aborts_without_yes_in_non_tty(mock_load_credentials) -> None:
    """Non-interactive stdin without --yes must exit 3 and never call smtp."""
    runner = CliRunner()
    with patch("pec_cli.main.send_pec") as send_mock:
        # CliRunner provides a non-TTY stdin by default — perfect for this.
        result = runner.invoke(
            cli,
            ["send", "--to", "dest@pec.it", "--subject", "x", "--body", "hi"],
        )
    assert result.exit_code == 3, result.output
    send_mock.assert_not_called()
    assert "non-interactive" in result.output.lower()


def test_pec_send_interactive_confirmation_aborts_on_no(
    mock_load_credentials,
) -> None:
    """When the user types 'n' at the confirm prompt, we abort cleanly (exit 0)."""
    runner = CliRunner()
    with (
        patch("pec_cli.main.send_pec") as send_mock,
        # Force the isatty check used in main.send to think we're interactive.
        patch("pec_cli.main._stdin_is_interactive", return_value=True),
    ):
        result = runner.invoke(
            cli,
            ["send", "--to", "dest@pec.it", "--subject", "x", "--body", "hi"],
            input="n\n",
        )
    assert result.exit_code == 0, result.output
    send_mock.assert_not_called()
    assert "aborted" in result.output.lower()


def test_pec_send_with_yes_calls_smtp(mock_load_credentials) -> None:
    """--yes bypasses the prompt and actually invokes send_pec."""
    runner = CliRunner()
    fake_result = {
        "status": "sent",
        "to": ["dest@pec.it"],
        "cc": [],
        "subject": "x",
        "message_id": "<abc@mayai-pec-cli>",
        "attachments": [],
    }
    with patch("pec_cli.main.send_pec", return_value=fake_result) as send_mock:
        result = runner.invoke(
            cli,
            [
                "send",
                "--to",
                "dest@pec.it",
                "--subject",
                "x",
                "--body",
                "hi",
                "--yes",
            ],
        )
    assert result.exit_code == 0, result.output
    send_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Message-ID idempotency
# ---------------------------------------------------------------------------


def test_message_id_is_deterministic_for_same_content_same_minute() -> None:
    args = dict(
        from_addr="user@pec.it",
        to=["dest@pec.it"],
        cc=None,
        subject="hello",
        body="ciao",
        minute_bucket=1_000_000,
    )
    assert _build_message_id(**args) == _build_message_id(**args)


def test_message_id_changes_with_content() -> None:
    base = dict(
        from_addr="user@pec.it",
        to=["dest@pec.it"],
        cc=None,
        subject="hello",
        body="ciao",
        minute_bucket=1_000_000,
    )
    assert _build_message_id(**base) != _build_message_id(
        **{**base, "body": "ciao!"}
    )
    assert _build_message_id(**base) != _build_message_id(
        **{**base, "subject": "other"}
    )
    assert _build_message_id(**base) != _build_message_id(
        **{**base, "to": ["other@pec.it"]}
    )


def test_message_id_changes_across_minutes() -> None:
    a = _build_message_id(
        from_addr="user@pec.it",
        to=["dest@pec.it"],
        cc=None,
        subject="s",
        body="b",
        minute_bucket=1_000_000,
    )
    b = _build_message_id(
        from_addr="user@pec.it",
        to=["dest@pec.it"],
        cc=None,
        subject="s",
        body="b",
        minute_bucket=1_000_001,
    )
    assert a != b


def test_message_id_has_rfc5322_shape() -> None:
    mid = _build_message_id(
        from_addr="user@pec.it",
        to=["dest@pec.it"],
        cc=None,
        subject="s",
        body="b",
    )
    assert mid.startswith("<") and mid.endswith(">")
    assert "@" in mid
