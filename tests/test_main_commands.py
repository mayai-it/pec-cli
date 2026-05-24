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


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_with_subject_field(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search_by_field.return_value = ["7"]
    client.fetch_summaries.return_value = [
        MessageSummary(
            id="7",
            date="2026-04-01T09:00:00",
            from_addr="inps@pec.it",
            to_addrs=["user@pec.it"],
            subject="INPS notice",
            pec_type=None,
            unread=True,
            has_attachments=False,
        )
    ]
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["--json", "search", "INPS", "--field", "subject"]
        )
    assert result.exit_code == 0, result.output
    client.search_by_field.assert_called_once_with("INPS", field="subject", since=None)
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert rows[0]["subject"] == "INPS notice"


def test_search_with_all_field_default(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search_by_field.return_value = []
    client.fetch_summaries.return_value = []
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["search", "tax"])
    assert result.exit_code == 0
    # Default field is "all".
    client.search_by_field.assert_called_once_with("tax", field="all", since=None)


def test_search_with_from_date_propagates(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.search_by_field.return_value = []
    client.fetch_summaries.return_value = []
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["search", "INPS", "--from-date", "2026-04-01"]
        )
    assert result.exit_code == 0
    client.search_by_field.assert_called_once_with(
        "INPS", field="all", since="2026-04-01"
    )


def test_search_surfaces_imap_error_with_exit_1(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    from pec_cli.imap.client import IMAPError

    client, _cls = imap_mock
    client.search_by_field.side_effect = IMAPError("server tired")
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["search", "anything"])
    assert result.exit_code == 1
    assert "server tired" in result.output


# ---------------------------------------------------------------------------
# list-folders
# ---------------------------------------------------------------------------


def test_list_folders_without_counts(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.list_folders.return_value = ["INBOX", "Sent", "[PEC]/Ricevute"]
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "list-folders"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert [r["name"] for r in rows] == ["INBOX", "Sent", "[PEC]/Ricevute"]


def test_list_folders_with_counts_includes_messages_and_unseen(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.list_folders.return_value = ["INBOX", "Sent"]
    client.folder_status.side_effect = [
        {"messages": 1234, "unseen": 47},
        {"messages": 823, "unseen": 0},
    ]
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "list-folders", "--counts"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert rows[0] == {"name": "INBOX", "messages": 1234, "unseen": 47}


def test_list_folders_with_counts_handles_non_selectable_folder(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    from pec_cli.imap.client import IMAPError

    client, _cls = imap_mock
    client.list_folders.return_value = ["[PEC]", "[PEC]/Ricevute"]
    # `[PEC]` is a Noselect parent — status fails on it.
    client.folder_status.side_effect = [
        IMAPError("noselect"),
        {"messages": 12, "unseen": 1},
    ]
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "list-folders", "--counts"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert rows[0]["messages"] is None
    assert rows[1]["messages"] == 12


# ---------------------------------------------------------------------------
# mark-read / mark-unread
# ---------------------------------------------------------------------------


def test_mark_read_calls_set_seen_true(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "mark-read", "42"])
    assert result.exit_code == 0, result.output
    client.set_seen.assert_called_once_with("42", seen=True)
    # Source folder must be opened read-write to STORE.
    client.select_folder.assert_called_once_with("inbox", readonly=False)
    payload = json.loads(result.output.strip())
    assert payload["action"] == "mark-read"
    assert payload["success"] is True


def test_mark_unread_calls_set_seen_false(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["--json", "mark-unread", "42"])
    assert result.exit_code == 0, result.output
    client.set_seen.assert_called_once_with("42", seen=False)


def test_mark_read_idempotent_when_imap_returns_ok(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    """Calling mark-read on an already-seen message succeeds silently —
    IMAP STORE +FLAGS is idempotent by spec."""
    client, _cls = imap_mock
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(cli, ["mark-read", "42"])
        result2 = runner.invoke(cli, ["mark-read", "42"])
    assert result.exit_code == 0
    assert result2.exit_code == 0


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


def test_move_validates_destination_exists(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.folder_exists.return_value = False
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["move", "42", "--to", "Nonexistent"]
        )
    assert result.exit_code == 1
    assert "not found" in result.output
    client.move_message.assert_not_called()


def test_move_uses_move_message_on_existing_destination(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.folder_exists.return_value = True
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli, ["--json", "move", "42", "--to", "Archive"]
        )
    assert result.exit_code == 0, result.output
    client.select_folder.assert_called_once_with("inbox", readonly=False)
    client.move_message.assert_called_once_with("42", "Archive")
    payload = json.loads(result.output.strip())
    assert payload["action"] == "move"
    assert payload["to_folder"] == "Archive"


def test_move_with_custom_from_folder(
    runner: CliRunner, creds: Credentials, imap_mock
) -> None:
    client, _cls = imap_mock
    client.folder_exists.return_value = True
    with patch("pec_cli.main.load_credentials", return_value=creds):
        result = runner.invoke(
            cli,
            ["move", "42", "--to", "Archive", "--from", "Sent"],
        )
    assert result.exit_code == 0
    client.select_folder.assert_called_once_with("Sent", readonly=False)


# ---------------------------------------------------------------------------
# Verbose / logging
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
