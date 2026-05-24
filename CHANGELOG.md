# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-05-24

### Added
- New read/manage commands and corresponding MCP tools:
  - `pec search QUERY [--field subject|from|body|all] [--folder F] [--from-date DATE]`
    + `pec_search` MCP tool
  - `pec list-folders [--counts]` + `pec_list_folders` MCP tool
  - `pec mark-read <id>` + `pec_mark_read` MCP tool
  - `pec mark-unread <id>` + `pec_mark_unread` MCP tool
  - `pec move <id> --to FOLDER [--from FOLDER]` + `pec_move` MCP tool
    (validates destination, uses IMAP `MOVE` with `COPY`+`EXPUNGE` fallback)
- Safety guards on `pec send` (CLI): interactive TTY confirmation,
  `--yes` flag for non-TTY contexts (exit code `3` otherwise),
  `--dry-run` for validation-only.
- Safety guards on `pec_send` (MCP): required `confirm_legal_send=True`,
  per-session rate limit (3 sends per recipient per 5 minutes),
  `dry_run=True` mode, recipient regex + non-empty body validation.
- Deterministic `Message-ID` for SMTP sends (SHA-256 of
  `from|to|cc|subject|body|minute-bucket`) — enables PEC providers to
  deduplicate accidental retries.
- Retry with exponential backoff on transient IMAP / SMTP failures
  (`1s, 2s, 4s, ...` capped at 30s, max 3 retries). New module
  `pec_cli/retry.py` exposing `with_retry` and `with_retry_predicate`.
- Tests: 30 → 195 (195 passing in ~1.3s). Coverage: 30 % → 86 %.
- CI matrix: 3 OS × 3 Python = 9 jobs + dedicated `audit` job
  (`pip-audit`).
- mypy strict + extra flags (`disallow_subclassing_any`,
  `strict_equality`, `extra_checks`), blocking in CI.
- Italian `README.it.md`, separate `docs/` (AUTHENTICATION, PROVIDERS,
  FAQ), CONTRIBUTING.md, GitHub issue templates (bug + feature).

### Changed
- README polished (278 → 257 lines), decorative emoji removed,
  Engineering notes + Quality bar sections added.
- Installation flow simplified: `pip install` is the primary path;
  `make install` moved to the Development section.
- Retroactive `v0.1.0` git tag created on the commit that actually
  shipped the `mayai-pec-cli==0.1.0` PyPI release.

### Fixed
- `email.utils.parsedate_to_datetime` raises `ValueError` on Python 3.10+
  instead of returning `None` — the existing `if parsed is None` fallback
  was dead code and would have crashed on malformed dates. Replaced with
  `try/except (ValueError, TypeError)`. Found by mypy `warn_unreachable`.
- `imap.uid("search", None, ...)` was stringifying `None` to `"None"` on
  the wire (lenient servers tolerated it but it's not protocol-correct).
  Now passes criteria directly.
- SMTP retry predicate ordering: `smtplib.SMTPException` inherits from
  `OSError`, so the initial `isinstance(..., OSError)` check was matching
  permanent 5xx errors and looping on them. Predicate reordered to check
  `SMTPResponseException` first.

### Security
- XML parsing in `pec_cli/daticert.py` now uses `defusedxml` (was
  `xml.etree`). Defense in depth against XXE and billion-laughs
  payloads — daticert blobs are network-sourced even if the certification
  chain is trusted.
- `starlette>=1.0.1` pinned directly in project dependencies
  (PYSEC-2026-161 affects `starlette<1.0.1`, transitively pulled in by
  the `mcp` SDK with a loose `>=0.27` constraint).
- Removed 4 superfluous `# type: ignore[import-not-found]` comments on
  keyring imports — the `[[tool.mypy.overrides]]` block makes them
  redundant, and silent ignores hide real type problems.

## [0.1.0] — 2026-05-18 (retroactive tag)

Initial release. See PyPI
[`mayai-pec-cli==0.1.0`](https://pypi.org/project/mayai-pec-cli/0.1.0/).

- CLI + MCP server for Italian PEC.
- Multi-provider: Aruba, Legalmail (InfoCert), Namirial, Register.it,
  Poste Italiane, Pec.it.
- IMAP/SMTP over SSL/TLS, keyring-backed credentials storage with
  Fernet-encrypted file fallback.
- `daticert.xml` parsing for PEC receipt-chain tracing.
