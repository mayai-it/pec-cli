# pec-cli

Command-line client for **PEC** (Posta Elettronica Certificata — Italian
certified email), built for both humans and AI agents. Designed to be
context-efficient: the default output strips empty fields, and `--json`
produces NDJSON suitable for piping into LLMs or jq.

Talks to the standard IMAP/SMTP endpoints exposed by Italian PEC providers
(Aruba, Legalmail/InfoCert, Namirial, Register.it), all over SSL/TLS.

Part of [MayAI CLI](https://mayai.it).

## Requirements

- Python 3.11+
- A working PEC account from one of the supported providers

## Installation

From source:

```bash
git clone https://github.com/mayai-it/pec-cli.git
cd pec-cli
make install
```

Or directly with pip:

```bash
pip install -e .
```

For local development (adds `pytest`, `ruff`):

```bash
make dev
```

## Quick start

```bash
# 1. Authenticate (password prompted interactively, never passed as a flag)
pec auth login --address mia@pec.it --provider aruba

# 2. Verify
pec auth status

# 3. List the 20 most recent PECs in the inbox as NDJSON
pec --json list

# 4. Filter to unread, since a given date
pec --json list --unread --from 2025-01-01 --limit 50

# 5. Read a single message and save its attachments
pec get 1234 --save-attachments

# 6. Send a PEC with an attachment
pec send --to dest@pec.it --subject "Oggetto" --file body.txt --attach doc.pdf
```

## Command reference

| Command | Description |
|---|---|
| `pec auth login --address ADDR --provider P` | Prompt for password, verify via IMAP, save credentials (encrypted). |
| `pec auth status` | Show whether credentials are present. |
| `pec auth logout` | Delete saved credentials and the encryption key. |
| `pec list [--folder F] [--unread] [--from YYYY-MM-DD] [--limit N]` | List PEC messages (default folder `inbox`, default limit 20). |
| `pec get <id> [--save-attachments] [--out DIR]` | Fetch a single PEC by IMAP UID. |
| `pec send --to ADDR --subject S (--body T | --file F) [--attach F] [--cc ADDR] [--dry-run]` | Send a PEC; `--to`, `--cc`, `--attach` are repeatable. |

### Global flags

These work in any position (before or after the subcommand):

| Flag | Effect |
|---|---|
| `--json` | Emit one JSON object per line (NDJSON). |
| `--verbose` | Log IMAP/SMTP timings and certification metadata to stderr. |
| `-h`, `--help` | Show help for the current command. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Application error (network, send failure, bad arguments) |
| `2` | Not authenticated — run `pec auth login` |

## Supported providers

| Provider             | `--provider` | IMAP                          | SMTP                          |
|----------------------|--------------|-------------------------------|-------------------------------|
| Aruba PEC            | `aruba`      | `imaps.pec.aruba.it:993`      | `smtps.pec.aruba.it:465`      |
| Legalmail (InfoCert) | `legalmail`  | `imapmail.legalmail.it:993`   | `smtpmail.legalmail.it:465`   |
| Namirial             | `namirial`   | `imap.namirialpec.it:993`     | `smtp.namirialpec.it:465`     |
| Register.it          | `register`   | `imap.pec.register.it:993`    | `smtp.pec.register.it:465`    |

All providers use implicit SSL/TLS (IMAPS:993 / SMTPS:465). Username is the
full PEC address; the password is the one provided by the PEC provider.

## Authentication

PEC is plain IMAP/SMTP with SSL — there's no OAuth. `pec auth login`:

1. Prompts you for the password on stderr (never echoed, never on argv).
2. Verifies it by opening an IMAP connection and logging in.
3. Generates a Fernet key (if not already present) and encrypts the password
   with it.
4. Writes the encrypted blob plus address/provider metadata to
   `~/.config/mayai-cli/pec/credentials.json`, mode `0600`.
5. Stores the Fernet key alongside it at
   `~/.config/mayai-cli/pec/key.bin`, mode `0600`.

Both files are removed by `pec auth logout`. Keeping the key in a separate
file is defense in depth — it doesn't stop a local attacker who can read both
files, but a leaked `credentials.json` on its own is unusable.

The password is never written to plain disk and never accepted via a
command-line flag.

## Output format

- **Default** — compact human-readable text. Empty / null fields are stripped
  so terminal output stays scannable.
- **`--json`** — NDJSON. One object per line; lists stream one element per
  line so consumers can process incrementally.
- **`--verbose`** — adds protocol timing lines on stderr (e.g.
  `imap: connected to imaps.pec.aruba.it:993 as mia@pec.it (284ms)`), and
  surfaces the PEC certification attachments (`daticert.xml`,
  `postacert.eml`, `smime.p7s/p7m`) that are normally hidden.

Errors always go to stderr, prefixed with `error:`.

### What `pec list` returns

Each row carries the IMAP UID (`id`), a normalized ISO date, the sender, the
subject, the PEC type (`accettazione`, `consegna`, `errore`, `preavviso`, …)
when present, and read/attachment flags.

### What `pec get` returns

The full message — headers, plain-text body (and HTML with `--verbose`),
plus the attachment list. By default the certification XMLs are filtered
out; pass `--verbose` to include them or `--save-attachments` to write
everything to disk under `./attachments/` (override with `--out DIR`).

## Development

```bash
make dev       # install with dev extras
make test      # run pytest
make lint      # run ruff
make clean     # remove caches and build artifacts
```

## License

MIT — see [LICENSE](./LICENSE).
