"""End-to-end CLI tests driven by `click.testing.CliRunner`.

All IMAP/SMTP layers are mocked — no network, no real keyring writes.
Tests run in-process for speed and to dodge Windows-specific shell oddities.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pec_cli.auth.credentials import Credentials
from pec_cli.main import cli
from pec_cli.models.message import Attachment, Message, MessageSummary


@pytest.fixture
def creds() -> Credentials:
    return Credentials(address="user@pec.it", provider="aruba", password="secret")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("pec_cli.retry.time.sleep"):
        yield


@pytest.fixture
def imap_mock():
    """Patch IMAPClient at the import point used by `pec_cli.main`."""
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    with patch("pec_cli.main.IMAPClient", return_value=mock_client) as cls:
        yield mock_client, cls


# ---------------------------------------------------------------------------
# auth status / logout
# ---------------------------------------------------------------------------


def test_auth_status_no_credentials_exits_2(runner: CliRunner) -> None:
    with patch("pec_cli.main.load_credentials", return_value=None):
        result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 2
    assert '"authenticated": false' in result.output.lower() or "authenticated" in result.output


def test_auth_status_with_credentials_emits_provider_info(
    runner: CliRunner, creds: Credentials
) -> None:
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["authenticated"] is True
    assert payload["address"] == "user@pec.it"
    assert payload["provider"] == "aruba"


def test_auth_logout_emits_removed_flag(runner: CliRunner) -> None:
    with patch("pec_cli.main.delete_credentials", return_value=True):
        result = runner.invoke(cli, ["--json", "auth", "logout"])
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["removed"] is True


# ---------------------------------------------------------------------------
# auth login
# ---------------------------------------------------------------------------


def test_auth_login_verifies_via_imap_then_saves(
    runner: CliRunner, imap_mock
) -> None:
    _client, _cls = imap_mock
    with (
        patch("pec_cli.main.save_credentials") as save_mock,
    ):
        result = runner.invoke(
            cli,
            ["auth", "login", "--address", "user@pec.it", "--provider", "aruba"],
            input="mypassword\n",
        )
    assert result.exit_code == 0, result.output
    save_mock.assert_called_once()
    saved_creds = save_mock.call_args.args[0]
    assert saved_creds.address == "user@pec.it"
    assert saved_creds.password == "mypassword"


def test_auth_login_aborts_when_imap_verification_fails(
    runner: CliRunner,
) -> None:
    from pec_cli.imap.client import IMAPError

    failing_client = MagicMock()
    failing_client.__enter__.side_effect = IMAPError("auth failed")
    with (
        patch("pec_cli.main.IMAPClient", return_value=failing_client),
        patch("pec_cli.main.save_credentials") as save_mock,
    ):
        result = runner.invoke(
            cli,
            ["auth", "login", "--address", "user@pec.it", "--provider", "aruba"],
            input="badpassword\n",
        )
    assert result.exit_code == 2
    save_mock.assert_not_called()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_default_runs_with_inbox_folder(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search.return_value = ["1"]
    client.fetch_summaries.return_value = [
        MessageSummary(
            id="1",
            date="2026-03-21T10:00:00",
            from_addr="alice@pec.it",
            to_addrs=["bob@pec.it"],
            subject="hello",
            pec_type=None,
            unread=True,
            has_attachments=False,
        )
    ]
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "list"])
    assert result.exit_code == 0, result.output
    client.select_folder.assert_called_once_with("inbox")
    # One row of JSON output.
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert rows[0]["from"] == "alice@pec.it"


def test_list_with_date_filter_passes_since(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search.return_value = []
    client.fetch_summaries.return_value = []
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["list", "--from", "2025-01-01", "--unread"]
        )
    assert result.exit_code == 0
    client.search.assert_called_once_with(unread=True, since="2025-01-01")


def test_list_surfaces_imap_error_with_exit_1(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    from pec_cli.imap.client import IMAPError

    client, _cls = imap_mock
    client.select_folder.side_effect = IMAPError("no such folder")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["list"])
    assert result.exit_code == 1
    assert "no such folder" in result.output


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def _fake_message(*, with_attachment: bool = False) -> Message:
    atts: list[Attachment] = []
    if with_attachment:
        atts = [
            Attachment(
                filename="invoice.pdf",
                content_type="application/pdf",
                size=4,
                data=b"PDF!",
            )
        ]
    return Message(
        id="42",
        date="2026-03-21T10:00:00",
        from_addr="alice@pec.it",
        to_addrs=["bob@pec.it"],
        cc_addrs=[],
        subject="hello",
        pec_type=None,
        body_text="hi",
        body_html=None,
        attachments=atts,
        daticert=None,
    )


def test_get_emits_message_payload(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.fetch_message.return_value = _fake_message()
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "get", "42"])
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["subject"] == "hello"


def test_get_save_attachments_writes_files(
    runner: CliRunner, creds: Credentials, imap_mock, tmp_path: Path
) -> None:
    client, _cls = imap_mock
    client.fetch_message.return_value = _fake_message(with_attachment=True)
    target = tmp_path / "attachments"
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli,
            ["--json", "get", "42", "--save-attachments", str(target)],
        )
    assert result.exit_code == 0, result.output
    assert (target / "invoice.pdf").read_bytes() == b"PDF!"
    payload = json.loads(result.output.strip())
    assert any("invoice.pdf" in p for p in payload["saved_attachments"])


def test_get_surfaces_imap_error_with_exit_1(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    from pec_cli.imap.client import IMAPError

    client, _cls = imap_mock
    client.fetch_message.side_effect = IMAPError("not found")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["get", "99"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# send — non-safety paths not yet covered in test_send_safeguards.py
# ---------------------------------------------------------------------------


def test_send_requires_body_or_file(
    runner: CliRunner, creds: Credentials
) -> None:
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["send", "--to", "dest@pec.it", "--subject", "x"]
        )
    assert result.exit_code == 1
    assert "either --body or --file" in result.output


def test_send_body_and_file_are_mutually_exclusive(
    runner: CliRunner, creds: Credentials, tmp_path: Path
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("hi")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli,
            [
                "send",
                "--to",
                "dest@pec.it",
                "--subject",
                "x",
                "--body",
                "inline",
                "--file",
                str(body_file),
            ],
        )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_send_with_body_file_reads_content(
    runner: CliRunner, creds: Credentials, tmp_path: Path
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("file body content")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli,
            [
                "send",
                "--to",
                "dest@pec.it",
                "--subject",
                "x",
                "--file",
                str(body_file),
                "--dry-run",
            ],
        )
    assert result.exit_code == 0, result.output
    # `file body content` is 17 chars — dry-run reports body_length.
    assert "body_length: 17" in result.output


def test_send_dry_run_with_attachment_lists_it(
    runner: CliRunner, creds: Credentials, tmp_path: Path
) -> None:
    att = tmp_path / "doc.pdf"
    att.write_bytes(b"PDFdata")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli,
            [
                "--json",
                "send",
                "--to",
                "dest@pec.it",
                "--subject",
                "x",
                "--body",
                "hi",
                "--attach",
                str(att),
                "--dry-run",
            ],
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert any("doc.pdf" in a for a in payload["attachments"])


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------


def test_trace_empty_message_id_exits_1(
    runner: CliRunner, creds: Credentials
) -> None:
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["trace", "<>"])
    assert result.exit_code == 1
    assert "empty" in result.output


def test_trace_returns_empty_chain_when_no_match(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search.return_value = []
    client.fetch_summaries.return_value = []
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["--json", "trace", "missing-msg-id@example.com"]
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["count"] == 0
    assert payload["events"] == []


# ---------------------------------------------------------------------------
# Global flags
# ---------------------------------------------------------------------------


def test_verbose_flag_enables_pec_logging_handler() -> None:
    """When --verbose is passed, a StreamHandler is attached to the pec logger."""
    import logging

    runner = CliRunner()
    # Clear any previously-attached handler (test isolation).
    pec_logger = logging.getLogger("pec")
    pec_logger.handlers.clear()

    with patch("pec_cli.main.load_credentials", return_value=None):
        runner.invoke(cli, ["--verbose", "auth", "status"])

    assert any(isinstance(h, logging.StreamHandler) for h in pec_logger.handlers)
    pec_logger.handlers.clear()  # cleanup
