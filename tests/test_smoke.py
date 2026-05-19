"""Smoke tests — pure-function helpers that don't need a live PEC server."""

from __future__ import annotations

import json

import pytest

from pec_cli.auth import credentials as cred_mod
from pec_cli.auth.credentials import (
    PROVIDERS,
    Credentials,
    delete_credentials,
    get_provider,
    load_credentials,
    save_credentials,
)
from pec_cli.daticert import parse_daticert
from pec_cli.imap.client import _imap_date
from pec_cli.models.message import Attachment, Message, _is_cert_attachment


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


# ---------------------------------------------------------------------------
# daticert.xml parsing
# ---------------------------------------------------------------------------


_DATICERT_AVVENUTA = b"""<?xml version="1.0" encoding="UTF-8"?>
<postacert tipo="avvenuta-consegna" errore="nessuno">
  <intestazione>
    <mittente>sender@pec.it</mittente>
    <destinatari tipo="certificato">recipient@pec.it</destinatari>
    <oggetto>Test subject</oggetto>
  </intestazione>
  <dati>
    <gestore-emittente>Test PEC Provider</gestore-emittente>
    <data zona="+0100"><giorno>21/03/2026</giorno><ora>10:25:00</ora></data>
    <identificativo>opec123.20260321102500.12345.67.1.1@pec.it</identificativo>
    <msgid>&lt;original-msg-id@pec.it&gt;</msgid>
    <ricevuta tipo="completa"/>
  </dati>
</postacert>
"""


def test_parse_daticert_avvenuta_consegna() -> None:
    dc = parse_daticert(_DATICERT_AVVENUTA)
    assert dc is not None
    assert dc.tipo == "avvenuta-consegna"
    assert dc.mittente == "sender@pec.it"
    assert dc.destinatari == ["recipient@pec.it"]
    assert dc.oggetto == "Test subject"
    assert dc.identificativo == "opec123.20260321102500.12345.67.1.1@pec.it"
    assert dc.riferimento_message_id == "original-msg-id@pec.it"
    # ISO 8601 with timezone, derived from zona="+0100"
    assert dc.data == "2026-03-21T10:25:00+01:00"
    assert dc.errore == "nessuno"


def test_parse_daticert_serializes_clean_dict() -> None:
    dc = parse_daticert(_DATICERT_AVVENUTA)
    assert dc is not None
    d = dc.to_dict()
    # `errore == "nessuno"` is noise; should be hidden from to_dict
    assert "errore" not in d
    assert d["tipo"] == "avvenuta-consegna"
    assert d["riferimento_message_id"] == "original-msg-id@pec.it"


def test_parse_daticert_handles_errore_consegna() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<postacert tipo="errore-consegna" errore="no-dest">
  <intestazione>
    <mittente>sender@pec.it</mittente>
    <destinatari tipo="certificato">missing@pec.it</destinatari>
    <oggetto>Oops</oggetto>
  </intestazione>
  <dati>
    <data zona="+0100"><giorno>01/04/2026</giorno><ora>09:00:00</ora></data>
    <identificativo>err-id@pec.it</identificativo>
    <msgid>orig@pec.it</msgid>
  </dati>
</postacert>
"""
    dc = parse_daticert(xml)
    assert dc is not None
    assert dc.tipo == "errore-consegna"
    assert dc.errore == "no-dest"
    assert dc.to_dict()["errore"] == "no-dest"


@pytest.mark.parametrize("payload", [b"", b"<not-xml", b"<other-root/>"])
def test_parse_daticert_returns_none_for_garbage(payload: bytes) -> None:
    assert parse_daticert(payload) is None


def test_message_to_dict_exposes_pec_cert_type() -> None:
    dc = parse_daticert(_DATICERT_AVVENUTA)
    msg = Message(
        id="42",
        date="2026-03-21T10:25:00+01:00",
        from_addr="poste@pec.it",
        to_addrs=["sender@pec.it"],
        cc_addrs=[],
        subject="CONSEGNA: Test subject",
        pec_type="avvenuta-consegna",
        body_text="ricevuta di avvenuta consegna",
        body_html=None,
        attachments=[],
        daticert=dc,
    )
    d = msg.to_dict()
    assert d["pec_cert_type"] == "avvenuta-consegna"
    # parsed cert dict only shows up with include_cert
    assert "pec_cert" not in d
    d_full = msg.to_dict(include_cert=True)
    assert d_full["pec_cert"]["riferimento_message_id"] == "original-msg-id@pec.it"


def test_message_to_dict_no_daticert_omits_field() -> None:
    msg = Message(
        id="7",
        date="2026-03-21T10:25:00+01:00",
        from_addr="a@example.com",
        to_addrs=["b@example.com"],
        cc_addrs=[],
        subject="plain",
        pec_type=None,
        body_text="hello",
        body_html=None,
        attachments=[],
        daticert=None,
    )
    d = msg.to_dict()
    assert "pec_cert_type" not in d
    assert "pec_cert" not in d


# ---------------------------------------------------------------------------
# Credentials: keyring path + Fernet fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config(monkeypatch, tmp_path):
    """Redirect credentials/key paths to a temp dir for isolation."""
    cfg = tmp_path / "mayai-cli" / "pec"
    cfg.mkdir(parents=True)
    monkeypatch.setattr(cred_mod, "CONFIG_DIR", cfg)
    monkeypatch.setattr(cred_mod, "CREDENTIALS_PATH", cfg / "credentials.json")
    monkeypatch.setattr(cred_mod, "KEY_PATH", cfg / "key.bin")
    return cfg


def test_save_uses_keyring_when_available(monkeypatch, tmp_config) -> None:
    store: dict[tuple[str, str], str] = {}
    def _set(addr, pw):
        store[(cred_mod.KEYRING_SERVICE, addr)] = pw
        return True

    monkeypatch.setattr(cred_mod, "_keyring_set", _set)
    monkeypatch.setattr(
        cred_mod, "_keyring_get",
        lambda addr: store.get((cred_mod.KEYRING_SERVICE, addr)),
    )
    monkeypatch.setattr(
        cred_mod, "_keyring_delete",
        lambda addr: store.pop((cred_mod.KEYRING_SERVICE, addr), None) is not None,
    )

    save_credentials(Credentials(address="me@pec.it", provider="aruba", password="hunter2"))

    # No key.bin should be created when keyring works
    assert not cred_mod.KEY_PATH.exists()
    payload = json.loads(cred_mod.CREDENTIALS_PATH.read_text())
    assert payload["password_storage"] == "keyring"
    assert "password_enc" not in payload
    # Password lives in keyring store
    assert store[(cred_mod.KEYRING_SERVICE, "me@pec.it")] == "hunter2"

    loaded = load_credentials()
    assert loaded is not None
    assert loaded.password == "hunter2"

    assert delete_credentials() is True
    assert not cred_mod.CREDENTIALS_PATH.exists()
    assert (cred_mod.KEYRING_SERVICE, "me@pec.it") not in store


def test_save_falls_back_to_fernet_when_keyring_unavailable(monkeypatch, tmp_config) -> None:
    monkeypatch.setattr(cred_mod, "_keyring_set", lambda addr, pw: False)
    monkeypatch.setattr(cred_mod, "_keyring_get", lambda addr: None)
    monkeypatch.setattr(cred_mod, "_keyring_delete", lambda addr: False)

    save_credentials(Credentials(address="me@pec.it", provider="aruba", password="hunter2"))

    assert cred_mod.KEY_PATH.exists()
    payload = json.loads(cred_mod.CREDENTIALS_PATH.read_text())
    assert payload["password_storage"] == "fernet"
    assert "password_enc" in payload

    loaded = load_credentials()
    assert loaded is not None
    assert loaded.password == "hunter2"


def test_keyring_login_migrates_existing_key_bin(monkeypatch, tmp_config) -> None:
    """An old install has key.bin on disk; on next save_credentials we should
    move the password into the keyring and remove key.bin."""
    # Simulate a pre-existing Fernet key (legacy install)
    legacy_key = cred_mod.Fernet.generate_key()
    cred_mod.KEY_PATH.write_bytes(legacy_key)

    store: dict[tuple[str, str], str] = {}
    def _set(addr, pw):
        store[(cred_mod.KEYRING_SERVICE, addr)] = pw
        return True

    monkeypatch.setattr(cred_mod, "_keyring_set", _set)
    monkeypatch.setattr(
        cred_mod, "_keyring_get",
        lambda addr: store.get((cred_mod.KEYRING_SERVICE, addr)),
    )

    save_credentials(Credentials(address="me@pec.it", provider="aruba", password="hunter2"))

    payload = json.loads(cred_mod.CREDENTIALS_PATH.read_text())
    assert payload["password_storage"] == "keyring"
    # key.bin removed as part of migration
    assert not cred_mod.KEY_PATH.exists()
