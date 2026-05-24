# FAQ

## Where are my credentials stored?

By default in the system keyring (macOS Keychain, Linux Secret Service,
Windows DPAPI) under service name `mayai-cli-pec`. A small metadata file
sits at `~/.config/mayai-cli/pec/credentials.json` (mode `0600`). On
headless systems where no keyring is available, the password is Fernet-
encrypted with a key at `~/.config/mayai-cli/pec/key.bin`. See
[AUTHENTICATION.md](AUTHENTICATION.md) for the full layout.

## My provider isn't in the supported list — what do I do?

Open an issue with the IMAP and SMTP host/port pair, or add a preset
yourself in `pec_cli/auth/credentials.py`. Most Italian PEC providers use
the standard 993 / 465 implicit-TLS layout, so adding one is a few lines.
Detail: [PROVIDERS.md](PROVIDERS.md).

## SSL / TLS connection errors — `CERTIFICATE_VERIFY_FAILED`

`pec-cli` uses `ssl.create_default_context()`, which trusts the OS root
certificate store. If you get certificate errors on macOS, install the
certificates bundled with your Python (run the
`Install Certificates.command` shipped with the python.org installer). On
Linux distros the system CA bundle is usually fine; if it's missing or
stale, `pip install certifi --upgrade` and reinstall Python's `ssl`
module's reference to it.

We do **not** disable certificate verification — PEC is a legal channel,
sending or receiving over an unverified TLS connection defeats the point.

## Can I un-send a PEC after `pec send` succeeds?

No. PEC has the legal value of a registered letter — once the server
accepts the message, it's delivered to the certified chain and the
`accettazione` receipt has been issued. There is no provider-side recall
mechanism comparable to "unsend" in webmail.

That's why `pec send` defaults to interactive confirmation, requires
`--yes` in non-TTY contexts (exit code `3` otherwise), and the MCP
`pec_send` tool requires `confirm_legal_send=True`. Use `--dry-run` /
`dry_run=True` when you only need to validate.

## I added `pec-mcp` to my MCP client and the tools don't show up — why?

Three usual causes:

1. **Path**: the `command` in the MCP client config must be the absolute
   path to the installed entry point. Run `which pec-mcp` and paste the
   result verbatim — `~` and shell aliases are not expanded.
2. **Not authenticated**: the server refuses to start (exit code `2`) if
   no credentials are stored. Run `pec auth login` first, then restart the
   MCP client.
3. **Server logs**: pec-mcp writes diagnostics to stderr. In Claude
   Desktop, check
   `~/Library/Logs/Claude/mcp-server-pec.log`. The first line tells you
   which account it bound to or which precondition failed.

## How do I search for PECs from a specific sender?

```bash
pec search "inps@pec.it" --field from
```

`--field` is one of `subject`, `from`, `body`, `all` (default). `all` matches
the query in any of the three. Combine with `--from-date YYYY-MM-DD` to scope
to a time window, and `--folder` to look outside `INBOX`:

```bash
# Everything from INPS since April 2026
pec search "INPS" --field from --from-date 2026-04-01

# Anything mentioning "invoice" in the Sent folder
pec search "invoice" --folder sent
```

The MCP tool is `pec_search(query, folder, field, limit, from_date)` with the
same semantics.

## How do I move PECs to an archive folder?

```bash
# See what folders exist
pec list-folders --counts

# Move one message into an archive folder
pec move 1234 --to "[PEC]/Archivio"
```

`pec move` validates that the destination exists before touching the
source — if you typo the folder name you get an error, not a deleted
message. The CLI prefers IMAP `MOVE` (RFC 6851) and transparently falls
back to `COPY` + `STORE \Deleted` + `EXPUNGE` on older servers. The MCP
equivalent is `pec_move(message_id, to_folder, from_folder)`.

## What's the `Message-ID` rule for retries?

The `Message-ID` header on outgoing PECs is a SHA-256 of
`(from, to, cc, subject, body, minute-of-send)`, formatted as
`<{32-hex-chars}@mayai-pec-cli>`. Two `pec send` runs with identical
content within the same UTC minute produce the same id — the provider
sees one logical email, not two. Deliberate resends a minute later get a
distinct id. Pair `pec send`'s returned `message_id` with `pec trace` to
follow the acceptance / delivery chain.
