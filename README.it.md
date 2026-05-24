# pec-cli

Client da riga di comando per la **PEC** (Posta Elettronica Certificata),
pensato sia per agenti AI sia per sviluppatori italiani.

> English: [README.md](README.md) — versione completa.

Parla con i server IMAP/SMTP standard dei principali provider italiani
(Aruba, Legalmail/InfoCert, Namirial, Register.it, Poste Italiane, Pec.it),
tutto su SSL/TLS. Output di default compatto e human-readable; con `--json`
produce NDJSON per pipe verso jq o LLM.

## Requisiti

- Python 3.11+
- Un account PEC presso uno dei [provider supportati](docs/PROVIDERS.md)

## Installazione

```bash
pip install mayai-pec-cli
```

Installa il comando `pec` (e `pec-mcp` per i server MCP).

## Quick start

```bash
# 1. Autenticazione (la password viene chiesta a runtime, mai come flag)
pec auth login --address mia@pec.it --provider aruba

# 2. Verifica
pec auth status

# 3. Liste le ultime PEC in inbox in NDJSON
pec --json list

# 4. Solo non lette, da una certa data
pec --json list --unread --from 2025-01-01

# 5. Leggi un messaggio e salva gli allegati
pec get 1234 --save-attachments ./allegati

# 6. Traccia la catena di ricevute di un invio
pec trace 'opec123.20260321102500.12345.67.1.1@pec.it'

# 7. Invia una PEC (chiede conferma interattiva — PEC ha valore legale)
pec send --to dest@pec.it --subject "Oggetto" --file corpo.txt --attach doc.pdf
```

## Sicurezza

`pec send` è cancello: in shell interattiva chiede conferma, in shell non
interattiva richiede `--yes` esplicito, e il tool MCP `pec_send` richiede
`confirm_legal_send=True`. La PEC ha valore di raccomandata — non si torna
indietro dopo l'invio.

Le credenziali stanno nel keyring di sistema (Keychain su macOS, Secret
Service su Linux, DPAPI su Windows). Su sistemi senza keyring c'è il
fallback Fernet con file cifrato. Mai password come argomento, mai in
chiaro su disco. Dettagli: [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## Altro

Per riferimento completo (tutti i comandi, flag, retry, MCP server,
provider, contributi): vedi [README.md](README.md) e
[docs/](docs/).

## Licenza

MIT — vedi [LICENSE](./LICENSE).
