# Contributing to pec-cli

Thanks for your interest. This project is small enough that bug reports
with reproduction steps are as welcome as PRs.

## Setup

```bash
git clone https://github.com/mayai-it/pec-cli.git
cd pec-cli
python -m venv .venv && source .venv/bin/activate
make dev    # pip install -e ".[dev]"
make test   # pytest
make lint   # ruff
mypy pec_cli/
```

You need a real PEC account only if you're working on the live IMAP/SMTP
paths. The 151 tests run fully offline against mocks — see "Testing
discipline" below.

## PR checklist

Before opening a PR, please confirm:

- [ ] `ruff check pec_cli/ tests/` is clean.
- [ ] `mypy pec_cli/` reports `Success: no issues found`.
- [ ] `pytest tests/` is fully green.
- [ ] Coverage on the touched module is not lower than `main`. Run
      `pytest --cov=pec_cli --cov-report=term tests/` and check the row.
- [ ] If you added a public-facing change, the PR description includes a
      `Changelog` line ready to drop into `CHANGELOG.md` under
      `[Unreleased]`.
- [ ] No new `# type: ignore` without a specific error code and a
      one-line motivation in the surrounding comment.
- [ ] No real PEC credentials, passwords, message contents, or `daticert`
      blobs from production accounts in the diff or the test fixtures.

## Commit convention

We follow the loose [Conventional
Commits](https://www.conventionalcommits.org/) pattern. A few prefixes
covers most changes:

| Prefix | When |
|---|---|
| `feat:` | New user-visible behavior |
| `fix:` | Bug fix |
| `docs:` | README / docs / changelog only |
| `test:` | Test changes only |
| `refactor:` | Code restructure with no behavior change |
| `chore:` | Tooling, deps, CI |
| `security:` | Hardening, vuln fix |

Subject line ≤ 72 chars. Body is optional; when present, explain *why*,
not *what* — the diff already shows the what.

## Testing discipline

**Never send real PEC traffic from tests.** PEC has the legal value of a
registered letter; an accidental `pec send` from a test fixture against
a real account is a real legal communication with cost.

Patterns we enforce in the existing suite:

- IMAP and SMTP are exercised through `unittest.mock` with `MagicMock`
  standing in for `imaplib.IMAP4_SSL` and `smtplib.SMTP_SSL`.
- `time.sleep` is patched out via the `_no_sleep` fixture so retry tests
  don't actually wait.
- The MCP tool tests use a `_StubCtx` instead of a real FastMCP
  `Context` — the tool functions are plain Python and don't need the
  server lifespan to be running.
- Test fixtures that build a `Credentials` object use the literal value
  `"secret"` and an obviously-fake address like `user@pec.it`.

If you genuinely need to verify against a real provider, do it manually
on your own account from your shell — don't commit a test that does it.

## Reporting bugs

Use the [bug report
template](.github/ISSUE_TEMPLATE/bug_report.md). Include:

- pec-cli version (`pec --version`).
- OS + Python version.
- The exact command + the output (with `--verbose` if possible, redact
  any sensitive content from the headers).
- Whether you used `--json` or default text output.

## Proposing features

Use the [feature request
template](.github/ISSUE_TEMPLATE/feature_request.md). Lead with the use
case — "what are you trying to do" beats "implement X" — and we'll figure
out the shape together.
