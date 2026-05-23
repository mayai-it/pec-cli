# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `pec send` CLI: interactive TTY confirmation, `--yes` flag for
  non-interactive contexts, and exit code `3` when the safeguard refuses to
  send. `--dry-run` was already supported.
- MCP `pec_send`: explicit `confirm_legal_send` and `dry_run` parameters,
  per-recipient rate limit (3 sends per 5 minutes within an MCP session),
  recipient-address and empty-body validation.
- `Message-ID` is now set explicitly on outgoing PECs, deterministically
  derived from `(from, to, cc, subject, body, minute-bucket)` so that an
  immediate retry of the same content yields the same id (idempotent for
  accidental double-sends, distinct for deliberate resends in a later minute).
  The id is returned in the `pec send` output and logged to stderr under
  `--verbose`.
- README: safety note on `pec send` and exit code `3` documented.

### Security
- Replace `xml.etree.ElementTree` with `defusedxml` in `pec_cli/daticert.py`
  to harden the PEC certification XML parser against XXE / billion-laughs
  style payloads.
- Pin `starlette>=1.0.1` directly in project dependencies to override the
  loose transitive constraint from `mcp` (PYSEC-2026-161 affects
  `starlette<1.0.1`).

### Misc
- Retroactive `v0.1.0` git tag pointing at the commit that ships the
  `mayai-pec-cli==0.1.0` PyPI release, so future releases can be diffed
  against a real tag instead of a free-floating commit.

### Resilience
- New `pec_cli/retry.py` module with `with_retry` and
  `with_retry_predicate` helpers (stdlib only — no `tenacity`, no
  `backoff` lib).
- IMAP operations (`connect`, `select_folder`, `search`, `fetch_summaries`,
  `fetch_message`) now retry on `OSError`, `imaplib.IMAP4.abort`, and
  transient `IMAP4.error` (`TRYAGAIN`, `SERVERBUG`, `UNAVAILABLE`,
  `INUSE`). Auth failures and other permanent errors propagate
  immediately.
- SMTP send retries on `OSError`, `SMTPServerDisconnected`, and
  `SMTPResponseException` with 4xx codes. 5xx codes (including
  `SMTPAuthenticationError`) propagate immediately. The MIME message is
  built once before the retry loop so all attempts share the same
  deterministic Message-ID — provider-side deduplication still works.
- Retry events log to `pec.retry` at WARNING level; `--verbose` wires
  them to stderr.
- 24 new tests covering retry behavior (`test_retry.py`,
  `test_imap_retry.py`, `test_smtp_retry.py`), including explicit
  verification that the Message-ID is identical across consecutive
  retries.

## [0.1.0] - 2026-05-18

Initial release. See PyPI [`mayai-pec-cli==0.1.0`](https://pypi.org/project/mayai-pec-cli/0.1.0/).
