# Authentication

PEC is plain IMAP/SMTP with SSL — there's no OAuth. Authentication is just a
username (the full PEC address) and a password issued by the provider.

## `pec auth login`

```bash
pec auth login --address mia@pec.it --provider aruba
```

1. Prompts you for the password on stderr (never echoed, never on argv).
2. Verifies it by opening an IMAP connection and logging in. If the
   credentials are wrong, nothing is written to disk.
3. Stores the password in the **system keyring** — macOS Keychain, Linux
   Secret Service, or Windows Credential Locker (DPAPI) — under the service
   name `mayai-cli-pec` and the PEC address as the username.
4. Writes a small metadata file at
   `~/.config/mayai-cli/pec/credentials.json` (mode `0600`) recording the
   address, provider, and where the password lives (`password_storage:
   "keyring"`).

## Fernet fallback for headless / CI environments

If no keyring backend is available (typical on headless Linux servers and
CI runners), the CLI transparently falls back to **Fernet** symmetric
encryption:

- A 32-byte key is generated at `~/.config/mayai-cli/pec/key.bin` (mode
  `0600`).
- The encrypted password is stored inline in `credentials.json` under
  `password_enc`, with `password_storage: "fernet"`.
- The file permissions are enforced at every read/write — if the OS reports
  anything looser than `0600`, the CLI refuses to use the file.

You don't need to do anything to opt in to this fallback; it triggers
automatically when `keyring.set_password()` fails or the round-trip read
returns something other than what was written (some backends accept calls
silently — we verify).

## Migration: existing `key.bin` → keyring

If you upgraded from a release that didn't use the keyring yet, your
existing `key.bin` + encrypted password keep working. The next time you run
`pec auth login` successfully, the password is re-stored in the keyring and
`key.bin` is removed. The `credentials.json` discriminator switches from
`"fernet"` to `"keyring"` automatically.

## File locations

| Path | Contents | Mode |
|---|---|---|
| `~/.config/mayai-cli/pec/credentials.json` | Address, provider, storage discriminator, (Fernet only) encrypted password | `0600` |
| `~/.config/mayai-cli/pec/key.bin` | Fernet key (when keyring is unavailable; absent otherwise) | `0600` |
| System keyring entry | Plaintext password under service `mayai-cli-pec`, username = PEC address | OS-managed |

## `pec auth logout`

Clears the keyring entry and removes both `credentials.json` and any
`key.bin`. Idempotent — safe to run when nothing is stored.

## What is NOT done

- The password is **never** accepted via a command-line flag — flags end up
  in shell history and process listings.
- The password is **never** written to plain disk — even the Fernet path
  encrypts at rest.
- Environment variables (`PEC_PASSWORD`, etc.) are **not** read — same
  reason as flags.
