"""Smoke tests — pure-function helpers that don't need a live PEC server."""

from __future__ import annotations

import pytest

from pec_cli.auth.credentials import PROVIDERS, get_provider
from pec_cli.imap.client import _imap_date
from pec_cli.models.message import Attachment, _is_cert_attachment


@pytest.mark.parametrize(
    "name,imap_host,smtp_host",
    [
        ("aruba", "imaps.pec.aruba.it", "smtps.pec.aruba.it"),
        ("legalmail", "imapmail.legalmail.it", "smtpmail.legalmail.it"),
        ("namirial", "imap.namirialpec.it", "smtp.namirialpec.it"),
        ("register", "imap.pec.register.it", "smtp.pec.register.it"),
        ("poste", "imappec.poste.it", "smtppec.poste.it"),
        ("pec.it", "imap.pec.it", "smtp.pec.it"),
    ],
)
def test_provider_endpoints(name: str, imap_host: str, smtp_host: str) -> None:
    pc = get_provider(name)
    assert pc.imap_host == imap_host
    assert pc.imap_port == 993
    assert pc.smtp_host == smtp_host
    assert pc.smtp_port == 465


def test_provider_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_provider("nope")


def test_providers_dict_covers_all() -> None:
    assert set(PROVIDERS) == {"aruba", "legalmail", "namirial", "register", "poste", "pec.it"}


def test_imap_date_formats_iso_to_imap() -> None:
    assert _imap_date("2025-01-09") == "09-Jan-2025"
    assert _imap_date("2024-12-31") == "31-Dec-2024"


@pytest.mark.parametrize("bad", ["2025", "2025-13-01", "not-a-date"])
def test_imap_date_rejects_invalid(bad: str) -> None:
    from pec_cli.imap.client import IMAPError

    with pytest.raises(IMAPError):
        _imap_date(bad)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("daticert.xml", True),
        ("DATICERT.XML", True),
        ("postacert.eml", True),
        ("smime.p7s", True),
        ("smime.p7m", True),
        ("invoice.pdf", False),
        ("", False),
    ],
)
def test_is_cert_attachment(filename: str, expected: bool) -> None:
    att = Attachment(filename=filename, content_type="application/octet-stream", size=0)
    assert _is_cert_attachment(att) is expected
