[![CI](https://github.com/mayai-it/pec-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/mayai-it/pec-cli/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mayai-pec-cli.svg)](https://pypi.org/project/mayai-pec-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/mayai-pec-cli.svg)](https://pypi.org/project/mayai-pec-cli/)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Built for AI agents](https://img.shields.io/badge/Built%20for-AI%20agents-purple)](https://mayai.it)

> Italiano: [README.it.md](README.it.md) — versione ridotta.

# pec-cli

Command-line client for **PEC** (Posta Elettronica Certificata — Italian
certified email), built for both humans and AI agents. Designed to be
context-efficient: the default output strips empty fields, and `--json`
produces NDJSON suitable for piping into LLMs or jq.

Talks to the standard IMAP/SMTP endpoints exposed by Italian PEC providers
(Aruba, Legalmail/InfoCert, Namirial, Register.it, Poste Italiane, Pec.it),
all over SSL/TLS.

## Why this exists

PEC (Posta Elettronica Certificata) is the legal-email standard for Italian
businesses and professionals — used daily for invoices, official notices,
contracts, public administration communications. Every Italian SME has one.

Programmatic access is fragmented: each provider (Aruba, Poste, Legalmail,
Namirial, ...) ships its own SDK or webmail-only interface. Open-source tools
that let AI agents send, receive, and track PEC messages are essentially
non-existent.

`pec-cli` fills that gap:

- **Agent-friendly**: NDJSON output, stable exit codes, errors on stderr —
  pipe it into Claude, jq, or any LLM workflow.
- **Human-friendly**: compact text output, one command per common task
  (send, list, fetch, trace).
- **Italian-native**: built for the way Italian businesses actually use PEC
  — formal communications, legal evidence, document workflows.

Part of [MayAI](https://mayai.it).

## Engineering notes

- **Deterministic Message-ID for idempotent sends.** SHA-256 of
  `(from, to, cc, subject, body, minute-of-send)` — accidental retries
  collapse to the same id and providers deduplicate.
- **Multi-provider abstraction.** Aruba, Legalmail, Namirial, Register.it,
  Poste, Pec.it share one CLI; presets in [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **Retry with exponential backoff.** Transient IMAP / SMTP failures retry
  up to 3 times (`1s, 2s, 4s, ...` capped at 30s). Permanent failures
  (auth, SMTP 5xx) propagate immediately.
- **`defusedxml` for daticert.xml parsing.** Neutralizes XXE / billion-laughs
  even though the certification chain is trusted — the input is still
  network-sourced.
- **Mypy strict, zero unmotivated ignores.** Strict baseline +
  `disallow_subclassing_any`, `strict_equality`, `extra_checks`. The only
  `# type: ignore` comments live in test code, each with a specific code
  and a documented reason.

## Quality bar

- **151 tests**, multi-OS / multi-Python (Ubuntu / macOS / Windows × 3.11 /
  3.12 / 3.13) via GitHub Actions.
- **~86 % branch coverage** on the production module (`pec_cli/`), tracked
  in CI.
- **Credentials in the system keyring** (macOS Keychain, Linux Secret
  Service, Windows DPAPI). Fernet-encrypted file as a headless fallback.

## Requirements

- Python 3.11+
- A working PEC account from one of the [supported providers](docs/PROVIDERS.md)

## Installation

```bash
pip install mayai-pec-cli
```

Installs a single `pec` command (plus `pec-mcp` for MCP servers) on your
`PATH`.

## MCP Server

pec-cli ships with a native MCP server, letting AI agents like Claude access
your PEC inbox directly — no subprocess, no JSON parsing.

![MCP demo](docs/mcp-demo.png)

### Setup with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pec": {
      "command": "/path/to/pec-mcp"
    }
  }
}
```

Find your path with `which pec-mcp`.

### Compatible MCP clients

| Client | Status |
|--------|--------|
| Claude Desktop | Tested |
| Cursor | Compatible (same stdio config) |
| Continue (VS Code) | Compatible (same stdio config) |
| Zed | Compatible (same stdio config) |
| ChatGPT | MCP support coming soon |

### Available tools

| Tool | Description |
|------|-------------|
| `pec_list` | List messages (folder, unread_only, limit) |
| `pec_get` | Get full message with body and cert |
| `pec_send` | Send a PEC (requires `confirm_legal_send=True`) |
| `pec_trace` | Trace receipt chain by message ID |
| `pec_auth_status` | Check authentication status |

## Quick start

```bash
# Authenticate — password prompted interactively, never passed as a flag.
pec auth login --address mia@pec.it --provider aruba

# List recent PECs as NDJSON; filter unread / by date.
pec --json list --unread --from 2025-01-01 --limit 50

# Read one message and save its attachments.
pec get 1234 --save-attachments ./attachments

# Include the parsed daticert.xml certification.
pec get 1234 --cert --json

# Trace the acceptance / delivery chain for an original message id.
pec trace 'opec123.20260321102500.12345.67.1.1@pec.it'

# Send a PEC with attachment (interactive confirmation by default).
pec send --to dest@pec.it --subject "Oggetto" --file body.txt --attach doc.pdf
```

## Command reference

| Command | Description |
|---|---|
| `pec auth login --address ADDR --provider P` | Prompt for password, verify via IMAP, save credentials in the system keyring (Fernet-encrypted file as fallback). |
| `pec auth status` | Show whether credentials are present. |
| `pec auth logout` | Delete saved credentials (keyring entry + any local encryption key). |
| `pec list [--folder F] [--unread] [--from YYYY-MM-DD] [--limit N]` | List PEC messages (default folder `inbox`, default limit 20). |
| `pec get <id> [--folder F] [--save-attachments DIR] [--cert]` | Fetch a single PEC by IMAP UID; `--cert` includes the parsed `daticert.xml` certification; `--save-attachments` writes attachments to `DIR`. |
| `pec trace <message-id> [--folder F] [--limit N]` | Find every receipt in the folder whose `daticert.xml` references this message id, ordered chronologically (`accettazione` → `presa-in-carico` → `avvenuta-consegna` / `errore-consegna`). |
| `pec send --to ADDR --subject S (--body T \| --file F) [--attach F] [--cc ADDR] [--dry-run] [--yes]` | Send a PEC; `--to`, `--cc`, `--attach` are repeatable. See safety note below. |

### Safety note on `pec send`

PEC has the legal value of a registered letter (raccomandata). To avoid
accidental sends:

- Interactive TTY: `pec send` prompts for confirmation before contacting SMTP.
- Non-TTY (CI, pipes, scripts): requires `--yes` explicitly. Exit code `3`
  otherwise.
- `--dry-run` validates the message without contacting SMTP.
- The MCP `pec_send` requires `confirm_legal_send=True` and is rate-limited
  to 3 sends per recipient per 5 minutes per session.

The deterministic Message-ID means an immediate retry of identical content
collapses to one logical email — pair with `pec trace` to follow the chain.

### Resilience

Transient IMAP / SMTP failures (socket timeouts, server `[TRYAGAIN]`, SMTP
4xx) retry up to 3 times with `1s, 2s, 4s, ...` backoff capped at 30s.
Permanent failures (auth, SMTP 5xx) propagate immediately. SMTP retries
reuse the same MIME envelope built before the loop, so the Message-ID is
stable across attempts and providers deduplicate correctly. Pass
`--verbose` for retry events on stderr.

### Global flags

These work in any position (before or after the subcommand):

| Flag | Effect |
|---|---|
| `--json` | Emit one JSON object per line (NDJSON). |
| `--verbose` | Log IMAP/SMTP timings, retry events, and certification metadata to stderr. |
| `-h`, `--help` | Show help for the current command. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Application error (network, send failure, bad arguments) |
| `2` | Not authenticated — run `pec auth login` |
| `3` | Refused to send: non-interactive shell without `--yes` |

## Supported providers

Aruba PEC, Legalmail (InfoCert), Namirial, Register.it, Poste Italiane,
Pec.it. Endpoints and `--provider` values: [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Authentication

`pec auth login` prompts for the password on stderr (never echoed, never on
argv), verifies it via IMAP, and stores it in the **system keyring** under
service name `mayai-cli-pec`. On headless boxes without a keyring backend,
it transparently falls back to a Fernet-encrypted file at
`~/.config/mayai-cli/pec/credentials.json` (mode `0600`).

Full details — keyring fallback, key migration, file locations:
[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## Output format

- **Default** — compact human-readable text. Empty / null fields are stripped
  so terminal output stays scannable.
- **`--json`** — NDJSON. One object per line; lists stream one element per
  line so consumers can process incrementally.
- **`--verbose`** — adds protocol timing lines on stderr, surfaces hidden
  certification attachments (`daticert.xml`, `postacert.eml`,
  `smime.p7s/p7m`), and emits retry events from the `pec.retry` logger.

Errors always go to stderr, prefixed with `error:`.

## Development

```bash
git clone https://github.com/mayai-it/pec-cli.git
cd pec-cli
make dev       # install -e .[dev]
make test      # pytest
make lint      # ruff
```

Mypy is run via `mypy pec_cli/` (configured in `pyproject.toml`).
Contributing guide and PR checklist: [CONTRIBUTING.md](CONTRIBUTING.md).

## Help

- [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) — keyring, Fernet fallback, file locations
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — IMAP/SMTP endpoints per provider
- [docs/FAQ.md](docs/FAQ.md) — common questions and gotchas
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [Issues](https://github.com/mayai-it/pec-cli/issues) — bug reports and feature requests

## License

MIT — see [LICENSE](./LICENSE).
