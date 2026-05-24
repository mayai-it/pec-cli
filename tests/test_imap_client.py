"""Coverage tests for `pec_cli/imap/client.py`.

Strategy: most of the module is pure parsing of imaplib's quirky FETCH
response format and of MIME messages. We test the helpers directly with
hand-crafted bytes, then drive the `IMAPClient` class via a MagicMock
substituted for `imaplib.IMAP4_SSL` so we exercise the full control flow
without any network.
"""

from __future__ import annotations

import imaplib
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from pec_cli.auth.credentials import Credentials
from pec_cli.imap.client import (
    IMAPClient,
    IMAPError,
    _bodystructure_has_attachments,
    _decode,
    _decode_part,
    _extract_bodies,
    _extract_parens,
    _extract_token,
    _find_daticert,
    _first_addr,
    _format_date,
    _imap_date,
    _parse_addr_list,
    _parse_summary_response,
    _pec_type,
    _walk_attachments,
)
from pec_cli.models.message import Attachment


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("pec_cli.retry.time.sleep"):
        yield


@pytest.fixture
def creds() -> Credentials:
    return Credentials(address="user@pec.it", provider="aruba", password="secret")


# ---------------------------------------------------------------------------
# Header decoding helpers
# ---------------------------------------------------------------------------


def test_decode_handles_none() -> None:
    assert _decode(None) == ""


def test_decode_passes_through_plain_str() -> None:
    assert _decode("hello") == "hello"


def test_decode_bytes_utf8() -> None:
    assert _decode(b"ciao") == "ciao"


def test_decode_bytes_latin1_fallback() -> None:
    # 0xe8 is "è" in latin-1 but invalid utf-8 → must fall back gracefully.
    assert "è" in _decode(b"caff\xe8")


def test_decode_rfc2047_encoded_subject() -> None:
    # =?utf-8?B?…?= base64 encoding of "ciao mondo"
    out = _decode("=?utf-8?B?Y2lhbyBtb25kbw==?=")
    assert "ciao mondo" in out


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------


def test_parse_addr_list_empty() -> None:
    assert _parse_addr_list(None) == []
    assert _parse_addr_list("") == []


def test_parse_addr_list_single() -> None:
    assert _parse_addr_list("user@pec.it") == ["user@pec.it"]


def test_parse_addr_list_multi_with_display_names() -> None:
    result = _parse_addr_list('"Mario Rossi" <mario@pec.it>, luca@pec.it')
    assert "mario@pec.it" in result
    assert "luca@pec.it" in result


def test_first_addr_returns_first_or_decoded_raw() -> None:
    assert _first_addr("mario@pec.it") == "mario@pec.it"
    # When parsing fails to extract any address, fall back to decoded raw.
    assert _first_addr(None) == ""


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def test_format_date_iso() -> None:
    iso = _format_date("Mon, 21 Mar 2026 10:25:00 +0100")
    assert iso.startswith("2026-03-21T10:25:00")


def test_format_date_empty() -> None:
    assert _format_date(None) == ""
    assert _format_date("") == ""


def test_format_date_invalid_falls_back_to_decoded_raw() -> None:
    # Previously this path was dead code — see imap/client.py:_format_date.
    out = _format_date("not a real date string at all")
    assert "not a real date" in out


def test_imap_date_iso_to_imap() -> None:
    assert _imap_date("2025-01-05") == "05-Jan-2025"


@pytest.mark.parametrize("bad", ["2025", "2025-13-01", "2025-x-01", "not-a-date"])
def test_imap_date_rejects_invalid(bad: str) -> None:
    with pytest.raises(IMAPError):
        _imap_date(bad)


# ---------------------------------------------------------------------------
# PEC header extraction
# ---------------------------------------------------------------------------


def test_pec_type_from_x_ricevuta() -> None:
    msg = EmailMessage()
    msg["X-Ricevuta"] = "Accettazione"
    assert _pec_type(msg) == "accettazione"


def test_pec_type_from_x_trasporto_when_no_ricevuta() -> None:
    msg = EmailMessage()
    msg["X-Trasporto"] = "Posta-Certificata"
    assert _pec_type(msg) == "posta-certificata"


def test_pec_type_returns_none_for_plain_message() -> None:
    assert _pec_type(EmailMessage()) is None


# ---------------------------------------------------------------------------
# Attachment / body extraction
# ---------------------------------------------------------------------------


def _make_multipart_with_attachment() -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "a@pec.it"
    msg["To"] = "b@pec.it"
    msg["Subject"] = "test"
    msg.set_content("hello body")
    msg.add_attachment(
        b"<postacert></postacert>",
        maintype="application",
        subtype="xml",
        filename="daticert.xml",
    )
    return msg


def test_walk_attachments_lists_attachments_with_data_when_requested() -> None:
    msg = _make_multipart_with_attachment()
    atts = _walk_attachments(msg, load_bytes=True)
    assert len(atts) == 1
    assert atts[0].filename == "daticert.xml"
    assert atts[0].data == b"<postacert></postacert>"


def test_walk_attachments_drops_bytes_when_load_bytes_false() -> None:
    msg = _make_multipart_with_attachment()
    atts = _walk_attachments(msg, load_bytes=False)
    assert atts[0].data is None
    assert atts[0].size == len(b"<postacert></postacert>")


def test_extract_bodies_from_multipart_text_plain() -> None:
    msg = _make_multipart_with_attachment()
    text, html = _extract_bodies(msg)
    assert "hello body" in text
    assert html is None


def test_extract_bodies_from_plain_message() -> None:
    msg = EmailMessage()
    msg.set_content("just text")
    text, _html = _extract_bodies(msg)
    assert "just text" in text


def test_decode_part_handles_unknown_charset() -> None:
    msg = EmailMessage()
    msg.set_content("hi")
    # Force a bogus charset; _decode_part must fall back to utf-8.
    msg.replace_header("Content-Type", 'text/plain; charset="bogus-x"')
    out = _decode_part(msg)
    assert "hi" in out


# ---------------------------------------------------------------------------
# _find_daticert
# ---------------------------------------------------------------------------


def test_find_daticert_picks_xml_attachment() -> None:
    atts = [
        Attachment(filename="document.pdf", content_type="application/pdf", size=10),
        Attachment(
            filename="daticert.xml",
            content_type="application/xml",
            size=20,
            data=b'<?xml version="1.0"?><postacert tipo="avvenuta-consegna" '
            b'errore="nessuno"><intestazione><mittente>m@pec.it</mittente>'
            b"</intestazione></postacert>",
        ),
    ]
    dc = _find_daticert(atts)
    assert dc is not None
    assert dc.tipo == "avvenuta-consegna"


def test_find_daticert_returns_none_when_absent() -> None:
    atts = [Attachment(filename="doc.pdf", content_type="application/pdf", size=10)]
    assert _find_daticert(atts) is None


def test_find_daticert_skips_xml_attachment_without_data() -> None:
    atts = [
        Attachment(filename="daticert.xml", content_type="application/xml", size=0, data=None),
    ]
    assert _find_daticert(atts) is None


# ---------------------------------------------------------------------------
# IMAP response parsing
# ---------------------------------------------------------------------------


def test_extract_token_basic() -> None:
    assert _extract_token("UID 42 FLAGS (foo)", "UID") == "42"


def test_extract_token_missing_returns_none() -> None:
    assert _extract_token("FLAGS (\\Seen)", "UID") is None


def test_extract_parens_basic() -> None:
    out = _extract_parens("FLAGS (\\Seen \\Answered)", "FLAGS")
    assert out == "\\Seen \\Answered"


def test_extract_parens_nested() -> None:
    out = _extract_parens('BODYSTRUCTURE ("text" "plain" ("charset" "utf-8"))', "BODYSTRUCTURE")
    assert out is not None
    assert "text" in out and "utf-8" in out


def test_extract_parens_missing_returns_none() -> None:
    assert _extract_parens("FLAGS (a)", "BODYSTRUCTURE") is None


def test_bodystructure_has_attachments_true() -> None:
    assert _bodystructure_has_attachments('("attachment" "filename")') is True


def test_bodystructure_has_attachments_false() -> None:
    assert _bodystructure_has_attachments('("inline" "body")') is False


def test_parse_summary_response_skips_non_tuple_items() -> None:
    # Only bytes (the closing ')' lines from imaplib) — nothing to parse.
    result = _parse_summary_response([b")", b")"])
    assert result == []


def test_parse_summary_response_with_one_message() -> None:
    headers = (
        b"From: alice@pec.it\r\n"
        b"To: bob@pec.it\r\n"
        b"Date: Mon, 21 Mar 2026 10:00:00 +0000\r\n"
        b"Subject: hello\r\n"
        b"X-Ricevuta: accettazione\r\n"
        b"\r\n"
    )
    meta = b"1 (UID 42 FLAGS (\\Seen) BODYSTRUCTURE (\"text\" \"plain\") BODY[HEADER...] {123}"
    raw = [(meta, headers), b")"]

    summaries = _parse_summary_response(raw)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.id == "42"
    assert s.from_addr == "alice@pec.it"
    assert s.subject == "hello"
    assert s.pec_type == "accettazione"
    assert s.unread is False  # \Seen present


def test_parse_summary_response_marks_unread_when_seen_absent() -> None:
    headers = b"From: x@pec.it\r\nSubject: u\r\n\r\n"
    meta = b"1 (UID 7 FLAGS () BODY[HEADER...] {30}"
    summaries = _parse_summary_response([(meta, headers), b")"])
    assert summaries[0].unread is True


# ---------------------------------------------------------------------------
# IMAPClient driven by MagicMock-substituted IMAP4_SSL
# ---------------------------------------------------------------------------


def _make_imap_mock() -> MagicMock:
    """A MagicMock IMAP4_SSL with sensible default success responses."""
    m = MagicMock()
    m.login.return_value = ("OK", [b"LOGIN COMPLETED"])
    m.select.return_value = ("OK", [b"42"])
    m.uid.return_value = ("OK", [b""])
    m.logout.return_value = ("BYE", [b"logging out"])
    return m


def test_imap_client_context_manager_connects_and_closes(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        with IMAPClient(creds) as client:
            assert client._imap is fake_imap
    # close() must logout once and clear the handle.
    fake_imap.logout.assert_called_once()


def test_imap_client_close_swallows_logout_errors(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.logout.side_effect = imaplib.IMAP4.error("server gone")
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        # Must not raise.
        client.close()
    assert client._imap is None


def test_imap_client_close_on_unconnected_is_noop(creds: Credentials) -> None:
    # No connect() — close() should silently return.
    IMAPClient(creds).close()


def test_imap_client_search_returns_empty_on_no_results(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b""])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.search() == []


def test_imap_client_search_parses_uid_list(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b"1 5 9"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.search() == ["1", "5", "9"]


def test_imap_client_search_with_date_filter_passes_imap_date(
    creds: Credentials,
) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b""])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.search(since="2025-01-01")
    # The criteria are the *args after the command — verify the SINCE was
    # rendered in IMAP's DD-Mon-YYYY format.
    args = fake_imap.uid.call_args.args
    assert args[0] == "search"
    assert any("SINCE 01-Jan-2025" in a for a in args[1:])


def test_imap_client_search_raises_imap_error_on_no_ok(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("NO", [b"sorry"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError, match="search failed"):
            client.search()


def test_imap_client_select_folder_inbox_succeeds(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.select_folder("inbox") == "INBOX"


def test_imap_client_select_sent_tries_candidates_until_one_succeeds(
    creds: Credentials,
) -> None:
    fake_imap = _make_imap_mock()
    # First two candidates fail, third succeeds.
    fake_imap.select.side_effect = [
        ("NO", [b"no such mailbox"]),
        ("NO", [b"no such mailbox"]),
        ("OK", [b"5"]),
        ("OK", [b"5"]),  # buffer in case of extra call
    ]
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        selected = client.select_folder("sent")
    # The third candidate in _SENT_FOLDER_CANDIDATES is "INBOX/Sent".
    assert selected == "INBOX/Sent"


def test_imap_client_select_folder_raises_when_all_candidates_fail(
    creds: Credentials,
) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.select.return_value = ("NO", [b"no such mailbox"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError, match="could not select folder"):
            client.select_folder("sent")


def test_imap_client_fetch_message_parses_and_returns_message(
    creds: Credentials,
) -> None:
    raw_email = (
        b"From: alice@pec.it\r\n"
        b"To: bob@pec.it\r\n"
        b"Date: Mon, 21 Mar 2026 10:00:00 +0000\r\n"
        b"Subject: hello\r\n"
        b"\r\n"
        b"the body text\r\n"
    )
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [(b"7 (UID 7 BODY[]", raw_email), b")"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        msg = client.fetch_message("7")
    assert msg.id == "7"
    assert msg.from_addr == "alice@pec.it"
    assert msg.subject == "hello"
    assert "the body text" in msg.body_text


def test_imap_client_fetch_message_raises_on_not_ok(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("NO", [b"sorry"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError, match="could not fetch"):
            client.fetch_message("99")


def test_imap_client_fetch_message_raises_on_empty_body(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    # OK with no tuple containing bytes — empty body case.
    fake_imap.uid.return_value = ("OK", [(b"meta only",)])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError):
            client.fetch_message("42")


def test_imap_client_fetch_summaries_empty_uids_is_noop(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.fetch_summaries([]) == []
    # Must not have called uid() since there's nothing to fetch.
    assert all(call.args[0] != "fetch" for call in fake_imap.uid.call_args_list)


def test_imap_client_fetch_summaries_returns_parsed(creds: Credentials) -> None:
    headers = (
        b"From: x@pec.it\r\nTo: y@pec.it\r\n"
        b"Subject: greeting\r\nDate: Mon, 21 Mar 2026 09:00:00 +0000\r\n\r\n"
    )
    meta = b"1 (UID 1 FLAGS () BODYSTRUCTURE (\"text\" \"plain\") BODY[HEADER...] {30}"
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [(meta, headers), b")"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        summaries = client.fetch_summaries(["1"])
    assert len(summaries) == 1
    assert summaries[0].subject == "greeting"


def test_imap_client_operations_without_connect_raise(creds: Credentials) -> None:
    client = IMAPClient(creds)
    with pytest.raises(IMAPError, match="not connected"):
        client.search()


# ---------------------------------------------------------------------------
# search_by_field — OR criteria, SINCE, field routing
# ---------------------------------------------------------------------------


def test_search_by_field_subject_builds_subject_criterion(
    creds: Credentials,
) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b""])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.search_by_field("INPS", field="subject")
    args = fake_imap.uid.call_args.args
    assert args[0] == "search"
    assert args[1:] == ("SUBJECT", "INPS")


def test_search_by_field_all_builds_or_criteria(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b""])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.search_by_field("INPS", field="all")
    args = fake_imap.uid.call_args.args
    # OR SUBJECT q OR FROM q BODY q
    assert args[1:] == (
        "OR", "SUBJECT", "INPS", "OR", "FROM", "INPS", "BODY", "INPS",
    )


def test_search_by_field_with_since_appends_imap_date(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b""])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.search_by_field("hi", field="subject", since="2025-01-05")
    args = fake_imap.uid.call_args.args
    assert "SINCE" in args
    assert "05-Jan-2025" in args


def test_search_by_field_unknown_field_raises(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError, match="unknown search field"):
            client.search_by_field("hi", field="cc")


# ---------------------------------------------------------------------------
# list_folders / folder_status / folder_exists
# ---------------------------------------------------------------------------


def test_list_folders_parses_list_response(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Sent"',
            b'(\\HasChildren \\Noselect) "/" "[PEC]"',
            b'(\\HasNoChildren) "/" "[PEC]/Ricevute"',
        ],
    )
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        folders = client.list_folders()
    assert folders == ["INBOX", "Sent", "[PEC]", "[PEC]/Ricevute"]


def test_folder_status_parses_messages_and_unseen(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.status.return_value = (
        "OK",
        [b'"INBOX" (MESSAGES 1234 UNSEEN 47)'],
    )
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        st = client.folder_status("INBOX")
    assert st == {"messages": 1234, "unseen": 47}


def test_folder_status_quotes_folder_with_spaces(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.status.return_value = ("OK", [b'"x" (MESSAGES 0 UNSEEN 0)'])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.folder_status("Posta inviata")
    # Folder name contains a space → must be quoted per IMAP atom rules.
    call_args = fake_imap.status.call_args.args
    assert call_args[0].startswith('"') and call_args[0].endswith('"')


def test_folder_status_passes_plain_atoms_unquoted(creds: Credentials) -> None:
    """`[PEC]/Errori` is a valid IMAP atom — brackets and slash do NOT need
    quoting per RFC 3501, so we don't add noise."""
    fake_imap = _make_imap_mock()
    fake_imap.status.return_value = ("OK", [b'"x" (MESSAGES 0 UNSEEN 0)'])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.folder_status("[PEC]/Errori")
    call_args = fake_imap.status.call_args.args
    assert call_args[0] == "[PEC]/Errori"


def test_folder_exists_true_when_status_succeeds(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.status.return_value = ("OK", [b'"x" (MESSAGES 0 UNSEEN 0)'])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.folder_exists("anything") is True


def test_folder_exists_false_when_status_raises(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.status.return_value = ("NO", [b"no such mailbox"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        assert client.folder_exists("nope") is False


# ---------------------------------------------------------------------------
# set_seen and move_message
# ---------------------------------------------------------------------------


def test_set_seen_true_uses_plus_flags(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b"done"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.set_seen("42", seen=True)
    args = fake_imap.uid.call_args.args
    assert args == ("STORE", "42", "+FLAGS", "(\\Seen)")


def test_set_seen_false_uses_minus_flags(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b"done"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.set_seen("42", seen=False)
    args = fake_imap.uid.call_args.args
    assert args == ("STORE", "42", "-FLAGS", "(\\Seen)")


def test_set_seen_raises_when_store_not_ok(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("NO", [b"bad"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        with pytest.raises(IMAPError, match="STORE failed"):
            client.set_seen("42", seen=True)


def test_move_message_uses_imap_move_when_ok(creds: Credentials) -> None:
    fake_imap = _make_imap_mock()
    fake_imap.uid.return_value = ("OK", [b"moved"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.move_message("42", "Archive")
    # Only one uid() call — the MOVE — when supported.
    assert fake_imap.uid.call_count == 1
    args = fake_imap.uid.call_args.args
    assert args[0] == "MOVE" and args[1] == "42" and "Archive" in args[2]


def test_move_message_falls_back_to_copy_store_expunge_on_move_error(
    creds: Credentials,
) -> None:
    fake_imap = _make_imap_mock()
    # First call (MOVE) raises; subsequent COPY + STORE return OK.
    fake_imap.uid.side_effect = [
        imaplib.IMAP4.error("server doesn't support MOVE"),
        ("OK", [b"copied"]),
        ("OK", [b"flag set"]),
    ]
    fake_imap.expunge.return_value = ("OK", [b"expunged"])
    with patch("pec_cli.imap.client.imaplib.IMAP4_SSL", return_value=fake_imap):
        client = IMAPClient(creds)
        client.connect()
        client.move_message("42", "Archive")
    # MOVE → COPY → STORE → EXPUNGE
    assert fake_imap.uid.call_count == 3
    assert fake_imap.expunge.call_count == 1


# ---------------------------------------------------------------------------
# folder-name quoting + LIST parser edge cases
# ---------------------------------------------------------------------------


def test_parse_list_response_handles_quoted_names_with_spaces() -> None:
    from pec_cli.imap.client import _parse_list_response

    raw = [b'(\\HasNoChildren) "/" "Posta inviata"']
    assert _parse_list_response(raw) == ["Posta inviata"]


def test_parse_list_response_skips_non_bytes() -> None:
    from pec_cli.imap.client import _parse_list_response

    raw: list[object] = [None, b'(\\HasNoChildren) "/" "INBOX"']
    assert _parse_list_response(raw) == ["INBOX"]


def test_quote_folder_passes_through_ascii() -> None:
    from pec_cli.imap.client import _quote_folder

    assert _quote_folder("INBOX") == "INBOX"


def test_quote_folder_escapes_names_with_spaces() -> None:
    from pec_cli.imap.client import _quote_folder

    out = _quote_folder("Posta inviata")
    assert out == '"Posta inviata"'


def test_quote_folder_escapes_embedded_quotes_and_backslashes() -> None:
    from pec_cli.imap.client import _quote_folder

    out = _quote_folder('weird "name" with \\backslash')
    # Embedded `"` becomes `\"`, embedded `\` becomes `\\`, wrapped in quotes.
    assert out.startswith('"') and out.endswith('"')
    assert '\\"' in out
    assert "\\\\" in out
