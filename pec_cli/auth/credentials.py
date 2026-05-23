"""Persisted PEC credentials.

Password storage is layered:

1. **System keyring** (preferred) — macOS Keychain, Linux Secret Service,
   Windows DPAPI via the `keyring` library. Nothing sensitive on disk.
2. **Fernet-encrypted file** (fallback) — for headless boxes / CI where no
   keyring backend is available. Key lives in `~/.config/mayai-cli/pec/key.bin`
   (chmod 0600); the encrypted password rides inside `credentials.json`.

`credentials.json` carries a `password_storage` discriminator so we know
where the password actually lives, and `address` so the keyring lookup can
find it. Legacy files without the discriminator are treated as `fernet` and
migrated to keyring on the next `pec auth login`.
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

KEYRING_SERVICE = "mayai-cli-pec"


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
    "poste": ProviderConfig(
        name="poste",
        imap_host="imappec.poste.it",
        imap_port=993,
        smtp_host="smtppec.poste.it",
        smtp_port=465,
    ),
    "pec.it": ProviderConfig(
        name="pec.it",
        imap_host="imap.pec.it",
        imap_port=993,
        smtp_host="smtp.pec.it",
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
    password: str  # plaintext in memory; encrypted on disk or in OS keyring

    @property
    def provider_config(self) -> ProviderConfig:
        return get_provider(self.provider)


# ---------------------------------------------------------------------------
# Keyring helpers (best-effort; any failure falls back to Fernet)
# ---------------------------------------------------------------------------


def _keyring_set(address: str, password: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, address, password)
        # Some backends (e.g. `keyring.backends.fail.Keyring`) accept calls
        # silently; verify the round-trip so we know it actually worked.
        got = keyring.get_password(KEYRING_SERVICE, address)
        return got == password
    except Exception:
        return False


def _keyring_get(address: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, address)
    except Exception:
        return None


def _keyring_delete(address: str) -> bool:
    try:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(KEYRING_SERVICE, address)
            return True
        except keyring.errors.PasswordDeleteError:
            return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


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

    address = raw["address"]
    provider = raw["provider"]
    storage = raw.get("password_storage", "fernet")

    if storage == "keyring":
        password = _keyring_get(address)
        if password is None:
            raise RuntimeError(
                "credentials.json says password is stored in the system keyring "
                "but it can't be retrieved — run `pec auth login` to re-save it"
            )
        return Credentials(address=address, provider=provider, password=password)

    # Fernet path (legacy or fallback when no keyring backend is available)
    if not KEY_PATH.exists():
        raise RuntimeError(
            "credentials present but encryption key is missing — "
            "run `pec auth logout` and `pec auth login` again"
        )
    key = KEY_PATH.read_bytes()
    try:
        password = Fernet(key).decrypt(raw["password_enc"].encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("could not decrypt stored password — key/credentials mismatch") from exc

    return Credentials(address=address, provider=provider, password=password)


def save_credentials(creds: Credentials) -> None:
    _ensure_config_dir()

    payload: dict = {
        "address": creds.address,
        "provider": creds.provider,
    }

    if _keyring_set(creds.address, creds.password):
        payload["password_storage"] = "keyring"
        # If we successfully migrated to keyring, the on-disk Fernet key is no
        # longer needed — remove it so a fresh install never carries a key.bin.
        if KEY_PATH.exists():
            try:
                KEY_PATH.unlink()
            except OSError:
                pass
    else:
        key = _load_or_create_key()
        enc = Fernet(key).encrypt(creds.password.encode("utf-8")).decode("utf-8")
        payload["password_storage"] = "fernet"
        payload["password_enc"] = enc

    tmp = CREDENTIALS_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CREDENTIALS_PATH)


def delete_credentials() -> bool:
    removed = False
    address: str | None = None
    if CREDENTIALS_PATH.exists():
        try:
            with CREDENTIALS_PATH.open("r", encoding="utf-8") as fh:
                address = json.load(fh).get("address")
        except (OSError, json.JSONDecodeError):
            address = None
        CREDENTIALS_PATH.unlink()
        removed = True
    if KEY_PATH.exists():
        KEY_PATH.unlink()
        removed = True
    if address and _keyring_delete(address):
        removed = True
    return removed
