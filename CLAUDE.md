# pec-cli — Istruzioni specifiche

## Cosa fa
CLI per leggere e inviare PEC (Posta Elettronica Certificata) da terminale.
Permette a un agente AI di gestire comunicazioni PEC senza aprire il browser
o il client email.

## Come funziona la PEC tecnicamente
La PEC usa protocolli standard (IMAP/SMTP) ma con server certificati.
I principali provider italiani e i loro server:

| Provider | IMAP | SMTP |
|---|---|---|
| Aruba PEC | imaps.pec.aruba.it:993 | smtps.pec.aruba.it:465 |
| Legalmail (InfoCert) | imapmail.legalmail.it:993 | smtpmail.legalmail.it:465 |
| Namirial | imap.namirialpec.it:993 | smtp.namirialpec.it:465 |
| Register.it | imap.pec.register.it:993 | smtp.pec.register.it:465 |

Tutti usano SSL/TLS. Auth: username (indirizzo PEC completo) + password.

## Autenticazione
```bash
pec auth login --address mia@pec.it --provider aruba
# chiede la password interattivamente (mai passarla come flag)
# salva in ~/.config/mayai-cli/pec/credentials.json (password cifrata)
```

## Comandi da implementare (priorità)

### Lettura
```bash
pec list                          # lista ultime 20 PEC ricevute
pec list --folder sent            # PEC inviate
pec list --unread                 # solo non lette
pec list --from 2025-01-01        # filtro per data
pec get <id>                      # leggi una PEC (testo + allegati)
pec get <id> --save-attachments   # scarica allegati in ./attachments/
```

### Invio
```bash
pec send --to dest@pec.it --subject "Oggetto" --body "Testo"
pec send --to dest@pec.it --subject "Oggetto" --file body.txt --attach doc.pdf
```

### Auth
```bash
pec auth login --address xxx@pec.it --provider aruba
pec auth status
pec auth logout
```

## Struttura file
```
pec-cli/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Makefile
├── pec_cli/
│   ├── __init__.py
│   ├── main.py
│   ├── imap/
│   │   ├── __init__.py
│   │   └── client.py      # connessione IMAP + lettura
│   ├── smtp/
│   │   ├── __init__.py
│   │   └── sender.py      # invio SMTP
│   ├── auth/
│   │   ├── __init__.py
│   │   └── credentials.py # salvataggio credenziali cifrate
│   ├── models/
│   │   ├── __init__.py
│   │   └── message.py     # dataclass Messaggio PEC
│   └── output/
│       ├── __init__.py
│       └── formatter.py
└── tests/
```

## Note importanti
- La password va cifrata a riposo — usa `cryptography` (Fernet)
- Le PEC hanno ricevute di accettazione e consegna — mostrare lo stato nella lista
- Gli allegati PEC spesso includono file XML di certificazione — ignorarli nell'output normale, mostrarli con `--verbose`
- Testare con un account PEC reale (non esiste sandbox pubblica)
