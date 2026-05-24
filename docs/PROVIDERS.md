# Supported providers

All providers use implicit SSL/TLS (IMAPS on port 993, SMTPS on port 465).
Username is the full PEC address; the password is the one issued by the
provider.

| Provider             | `--provider` | IMAP                          | SMTP                          |
|----------------------|--------------|-------------------------------|-------------------------------|
| Aruba PEC            | `aruba`      | `imaps.pec.aruba.it:993`      | `smtps.pec.aruba.it:465`      |
| Legalmail (InfoCert) | `legalmail`  | `imapmail.legalmail.it:993`   | `smtpmail.legalmail.it:465`   |
| Namirial             | `namirial`   | `imap.namirialpec.it:993`     | `smtp.namirialpec.it:465`     |
| Register.it          | `register`   | `imap.pec.register.it:993`    | `smtp.pec.register.it:465`    |
| Poste Italiane       | `poste`      | `imappec.poste.it:993`        | `smtppec.poste.it:465`        |
| Pec.it               | `pec.it`     | `imap.pec.it:993`             | `smtp.pec.it:465`             |

## Missing a provider?

If your provider isn't listed, two paths:

1. **Open an issue** with the IMAP and SMTP hostnames + ports — most
   providers use the standard 993/465 layout and we just need the host.
2. **Add it locally**: the preset table lives in
   `pec_cli/auth/credentials.py` under `PROVIDERS`. Each entry is a
   `ProviderConfig(name, imap_host, imap_port, smtp_host, smtp_port)`.
   A PR adding the preset + a row to this table is welcome.

## Folder names by provider

The `sent` folder name varies between providers. `pec list --folder sent`
probes a small set of candidates so the same CLI works everywhere:

- `INBOX.Sent` (Aruba)
- `Sent` (Legalmail, Namirial — most IMAP-standard naming)
- `INBOX/Sent` (some older deployments)
- `Posta inviata` (Italian-localized folder names on some webmail-derived
  servers)

If your provider exposes a different name, pass it verbatim — `pec list
--folder "MyCustomFolder"` works.
