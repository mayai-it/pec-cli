"""Persisted PEC credentials with Fernet-encrypted password at rest.

Layout under ~/.config/mayai-cli/pec/:
    key.bin           # 32-byte url-safe Fernet key, mode 0600
    credentials.json  # JSON with address, provider, encrypted password, mode 0600

The key file is *not* a strong defense against a local attacker who can read
both files — it's defense in depth so a leaked credentials.json alone is
unusable. Both files are chmod 0600 so other users on the box can't read them.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

CONFIG_DIR = Path.home() / ".config" / "mayai-cli" / "pec"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
KEY_PATH = CONFIG_DIR / "key.bin"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int


PROVIDERS: dict[str, ProviderConfig] = {
    "aruba": ProviderConfig(
        name="aruba",
        imap_host="imaps.pec.aruba.it",
        imap_port=993,
        smtp_host="smtps.pec.aruba.it",
        smtp_port=465,
    ),
    "legalmail": ProviderConfig(
        name="legalmail",
        imap_host="imapmail.legalmail.it",
        imap_port=993,
        smtp_host="smtpmail.legalmail.it",
        smtp_port=465,
    ),
    "namirial": ProviderConfig(
        name="namirial",
        imap_host="imap.namirialpec.it",
        imap_port=993,
        smtp_host="smtp.namirialpec.it",
        smtp_port=465,
    ),
    "register": ProviderConfig(
        name="register",
        imap_host="imap.pec.register.it",
        imap_port=993,
        smtp_host="smtp.pec.register.it",
        smtp_port=465,
    ),
}


def get_provider(name: str) -> ProviderConfig:
    key = name.lower().strip()
    if key not in PROVIDERS:
        raise KeyError(f"unknown provider '{name}' (known: {', '.join(sorted(PROVIDERS))})")
    return PROVIDERS[key]


@dataclass
class Credentials:
    """A single PEC account's saved credentials."""

    address: str
    provider: str
    password: str  # plaintext in memory; encrypted on disk

    @property
    def provider_config(self) -> ProviderConfig:
        return get_provider(self.provider)


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_or_create_key() -> bytes:
    _ensure_config_dir()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    tmp = KEY_PATH.with_suffix(".tmp")
    tmp.write_bytes(key)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(KEY_PATH)
    return key


def load_credentials() -> Credentials | None:
    if not CREDENTIALS_PATH.exists():
        return None
    with CREDENTIALS_PATH.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not KEY_PATH.exists():
        # Encrypted blob exists but the key is gone — we can't recover.
        raise RuntimeError(
            "credentials present but encryption key is missing — "
            "run `pec auth logout` and `pec auth login` again"
        )
    key = KEY_PATH.read_bytes()
    try:
        password = Fernet(key).decrypt(raw["password_enc"].encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("could not decrypt stored password — key/credentials mismatch") from exc

    return Credentials(
        address=raw["address"],
        provider=raw["provider"],
        password=password,
    )


def save_credentials(creds: Credentials) -> None:
    _ensure_config_dir()
    key = _load_or_create_key()
    enc = Fernet(key).encrypt(creds.password.encode("utf-8")).decode("utf-8")

    payload = {
        "address": creds.address,
        "provider": creds.provider,
        "password_enc": enc,
    }

    tmp = CREDENTIALS_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CREDENTIALS_PATH)


def delete_credentials() -> bool:
    removed = False
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
        removed = True
    if KEY_PATH.exists():
        KEY_PATH.unlink()
        removed = True
    return removed
