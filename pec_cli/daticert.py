"""Parser for `daticert.xml` — the certified metadata attached to every PEC.

Every certified mail (PEC) carries a `daticert.xml` payload describing the
event the message represents: acceptance by the sender's provider, taking-in-
charge by the recipient's provider, successful delivery, or a delivery error.

The XML schema is set by DPCM 2 novembre 2005 (allegato tecnico). The relevant
fragment looks like:

    <postacert tipo="avvenuta-consegna" errore="nessuno">
      <intestazione>
        <mittente>mittente@pec.it</mittente>
        <destinatari tipo="certificato">dest@pec.it</destinatari>
        <oggetto>...</oggetto>
      </intestazione>
      <dati>
        <gestore-emittente>...</gestore-emittente>
        <data zona="+0100"><giorno>21/03/2026</giorno><ora>10:25:00</ora></data>
        <identificativo>opec123.20260321102500.12345.67.1.1@pec.it</identificativo>
        <msgid>&lt;original-message-id@pec.it&gt;</msgid>
        <ricevuta tipo="completa"/>
      </dati>
    </postacert>

We expose `parse_daticert(bytes) -> DatiCert | None` and keep the resulting
fields stable so they can be emitted as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element  # type only — parsing uses defusedxml

from defusedxml.ElementTree import ParseError, fromstring

KNOWN_TIPI = {
    "accettazione",
    "non-accettazione",
    "presa-in-carico",
    "avvenuta-consegna",
    "mancata-consegna",
    "errore-consegna",
    "preavviso-errore-consegna",
    "rilevazione-virus",
}


@dataclass
class DatiCert:
    tipo: str
    mittente: str
    destinatari: list[str]
    data: str  # ISO 8601 with timezone when available
    identificativo: str  # this receipt's message id
    riferimento_message_id: str  # original Message-ID this receipt refers to
    oggetto: str = ""
    errore: str | None = None

    def to_dict(self) -> dict:
        out: dict = {
            "tipo": self.tipo,
            "mittente": self.mittente,
            "destinatari": self.destinatari,
            "data": self.data,
            "identificativo": self.identificativo,
            "riferimento_message_id": self.riferimento_message_id,
        }
        if self.oggetto:
            out["oggetto"] = self.oggetto
        if self.errore and self.errore != "nessuno":
            out["errore"] = self.errore
        return out


def parse_daticert(xml_bytes: bytes) -> DatiCert | None:
    """Parse a daticert.xml blob. Return None if the document isn't recognizable."""
    if not xml_bytes:
        return None
    try:
        root = fromstring(xml_bytes)
    except ParseError:
        return None
    if root.tag != "postacert":
        return None

    tipo = (root.get("tipo") or "").strip().lower()
    errore = root.get("errore")

    intestazione = root.find("intestazione")
    dati = root.find("dati")

    mittente = ""
    oggetto = ""
    destinatari: list[str] = []
    if intestazione is not None:
        mittente = _text(intestazione, "mittente")
        oggetto = _text(intestazione, "oggetto")
        for d in intestazione.findall("destinatari"):
            if d.text and d.text.strip():
                destinatari.append(d.text.strip())

    identificativo = ""
    riferimento = ""
    data_iso = ""
    if dati is not None:
        identificativo = _text(dati, "identificativo")
        msgid_raw = _text(dati, "msgid")
        riferimento = msgid_raw.strip().lstrip("<").rstrip(">").strip()
        data_el = dati.find("data")
        if data_el is not None:
            data_iso = _format_data(data_el)

    return DatiCert(
        tipo=tipo,
        mittente=mittente,
        destinatari=destinatari,
        data=data_iso,
        identificativo=identificativo,
        riferimento_message_id=riferimento,
        oggetto=oggetto,
        errore=errore,
    )


def _text(parent: Element, tag: str) -> str:
    el = parent.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _format_data(data_el: Element) -> str:
    """Combine <giorno>DD/MM/YYYY</giorno><ora>HH:MM:SS</ora> + zona into ISO 8601."""
    giorno = _text(data_el, "giorno")
    ora = _text(data_el, "ora")
    zona = (data_el.get("zona") or "").strip()
    if not giorno or not ora:
        return ""
    try:
        dt = datetime.strptime(f"{giorno} {ora}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return f"{giorno} {ora} {zona}".strip()
    tz = _parse_zona(zona)
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    return dt.isoformat()


def _parse_zona(zona: str) -> timezone | None:
    """Parse PEC `zona` attribute (e.g. '+0100', '+01:00') into a tzinfo."""
    if not zona:
        return None
    z = zona.replace(":", "")
    if len(z) < 3 or z[0] not in "+-":
        return None
    sign = 1 if z[0] == "+" else -1
    try:
        hh = int(z[1:3])
        mm = int(z[3:5]) if len(z) >= 5 else 0
    except ValueError:
        return None
    return timezone(sign * timedelta(hours=hh, minutes=mm))
