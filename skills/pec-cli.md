---
name: pec-cli
description: Use whenever the user asks about reading, sending, or searching their PEC (Posta Elettronica Certificata) inbox, or mentions "PEC", "posta certificata", "Aruba PEC", "Legalmail", "Namirial". Provides a CLI to talk IMAPS/SMTPS with Italian certified email providers.
---

# pec-cli — agent usage guide

`pec` is a command-line client for **PEC** (Posta Elettronica Certificata —
Italian certified email). Use it any time the user asks you to read, search,
or send certified email.

## When to use this skill

Trigger on user prompts like:
- "Did I get a PEC from the Comune today?"
- "Send a PEC to legale@example.it with this attachment"
- "Show me unread PECs from this week"
- "What's the consegna receipt for PEC 1234?"
- "Forward last week's PECs from XYZ to me"
- "Scarica gli allegati della PEC 567"

## Golden rules

1. **Always pass `--json`** when you intend to parse the output. The default
   format is for humans; `--json` is NDJSON (one object per line) and is what
   you should consume.
2. **Check auth first** with `pec auth status` if you're unsure whether the
   user is logged in. Exit code `2` means not authenticated — tell the user
   to run `pec auth login --address mia@pec.it --provider aruba`.
3. **Never pass the password on the CLI.** `pec auth login` prompts for it
   interactively on stderr. Don't try to script it with `echo PASS | pec ...`
   — it won't work and it would leak the password into shell history.
4. **Use `--limit N`** when you only need a sample. The default is 20.
   Aggressive polling can get the account rate-limited at the provider level.
5. **Read stderr separately.** Errors go to stderr with the prefix `error:`.
   Exit codes: `0` ok, `1` application error, `2` not authenticated.
6. **Never echo the credentials file or the key.** They live at
   `~/.config/mayai-cli/pec/credentials.json` and
   `~/.config/mayai-cli/pec/key.bin`.
7. **Filter out PEC certification noise.** By default the CLI hides
   `daticert.xml`, `postacert.eml`, `smime.p7s/p7m` from `pec get`. Pass
   `--verbose` only if you specifically need them.

## Command cheat sheet

### Auth
```bash
pec auth login --address mia@pec.it --provider aruba
pec auth status
pec auth logout
```

Supported `--provider` values: `aruba`, `legalmail`, `namirial`, `register`.

### List
```bash
# Latest 20 inbox messages
pec --json list

# Sent items (folder name auto-resolves across providers)
pec --json list --folder sent

# Only unread
pec --json list --unread

# Since a given date (YYYY-MM-DD)
pec --json list --from 2025-01-01

# More than 20
pec --json list --limit 100

# Combinations
pec --json list --unread --from 2025-04-01 --limit 50
```

List row shape:
```json
{
  "id": "1234",
  "date": "2025-05-18T09:42:11+02:00",
  "from": "mittente@pec.it",
  "subject": "...",
  "pec_type": "posta-certificata",
  "unread": true,
  "has_attachments": true
}
```

`pec_type` values you'll typically see (from `X-Ricevuta` / `X-Trasporto`):
`accettazione`, `consegna`, `errore`, `preavviso`, `posta-certificata`,
`avvenuta-consegna`, `mancata-consegna`. `null` means the message is plain
email (rare in a real PEC mailbox — usually a misdirected non-PEC sender).

### Get
```bash
# Read one message
pec --json get 1234

# Save attachments to ./attachments/
pec --json get 1234 --save-attachments

# Save into a specific directory
pec --json get 1234 --save-attachments --out ./inbox/1234

# From the sent folder
pec --json get 1234 --folder sent
```

Detail shape:
```json
{
  "id": "1234",
  "date": "2025-05-18T09:42:11+02:00",
  "from": "mittente@pec.it",
  "to": ["mia@pec.it"],
  "cc": [],
  "subject": "...",
  "pec_type": "posta-certificata",
  "body": "plain-text body...",
  "attachments": [
    {"filename": "contratto.pdf", "content_type": "application/pdf", "size": 184320}
  ],
  "saved_attachments": ["attachments/contratto.pdf"]
}
```

`body_html` and the certification attachments only appear with `--verbose`.

### Send
```bash
# Inline body
pec send --to dest@pec.it --subject "Oggetto" --body "Testo della PEC"

# Body from file + attachment(s)
pec send --to dest@pec.it --subject "Oggetto" \
         --file body.txt --attach contratto.pdf

# Multiple recipients and CC
pec send --to a@pec.it --to b@pec.it --cc c@pec.it \
         --subject "Hi" --body "..."

# Dry-run: prints what would be sent, no SMTP traffic
pec send --to dest@pec.it --subject "Test" --body "Body" --dry-run
```

Send response shape:
```json
{
  "status": "sent",
  "to": ["dest@pec.it"],
  "cc": [],
  "subject": "Oggetto",
  "attachments": ["contratto.pdf"]
}
```

Dry-run replaces `status` with `"dry-run"` and adds `from` and `body_length`.

## Common workflows

### "Did I get a PEC from XYZ today?"
```bash
pec --json list --from "$(date -u +%Y-%m-%d)" \
  | jq 'select(.from | test("xyz"; "i"))'
```

### "Save all attachments from unread PECs since April"
```bash
pec --json list --unread --from 2025-04-01 \
  | jq -r .id \
  | while read -r id; do
      pec --json get "$id" --save-attachments --out "./attachments/$id"
    done
```

### "Reply with a counter-PEC to message 1234"
```bash
FROM=$(pec --json get 1234 | jq -r .from)
SUBJ=$(pec --json get 1234 | jq -r '.subject | "Re: " + .')
pec send --to "$FROM" --subject "$SUBJ" --file reply.txt
```

### "Debug why a send is failing"
Add `--verbose` to surface the SMTP protocol dialogue on stderr:
```bash
pec --verbose send --to dest@pec.it --subject "Test" --body "..." --dry-run
```

## Things this CLI does NOT do (yet)

- No reply/forward helpers — assemble those yourself with `pec get` + `pec send`.
- No search by sender / subject — pipe `pec --json list` through `jq`.
- No mark-as-read / mark-as-unread; listing uses `BODY.PEEK[...]` and never
  changes the `\Seen` flag.
- No delete / move / flag operations.
- No support for STARTTLS-on-25/587 providers — only implicit SSL on 993/465.

## Error patterns and what they mean

| stderr message | Likely cause |
|---|---|
| `error: not authenticated — run \`pec auth login\` first` (exit 2) | No credentials saved. |
| `error: IMAP login failed: ...` | Wrong password, locked account, or provider blocking. Re-login. |
| `error: could not reach <host>:993` | Network issue or the user picked the wrong `--provider`. |
| `error: could not select folder (tried 'Posta inviata')` | `--folder sent` could not match any known name; pass the raw folder name. |
| `error: SMTP authentication failed: ...` | Same credentials work for IMAP but SMTP rejects — usually a provider-side restriction (e.g. SMTP disabled). |
| `error: credentials present but encryption key is missing` (exit 2) | `key.bin` was deleted. Run `pec auth logout` then `pec auth login` again. |

## When in doubt

Run `pec --help`, `pec <command> --help`. The help text is the source of truth
for flags and arguments.
