"""Dataclasses for PEC messages and attachments.

PEC messages have two flavors of content compared to plain email:
- a `pec_type` header that signals receipts (accettazione, consegna, errore, ...)
- a `daticert.xml` attachment carrying certified metadata

`MessageSummary` is what `pec list` returns; `Message` is the full body used by
`pec get`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pec_cli.daticert import DatiCert


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int
    # raw bytes are kept off the dataclass when listing, attached only for get
    data: bytes | None = None

    def to_dict(self, include_size: bool = True) -> dict:
        out: dict = {"filename": self.filename, "content_type": self.content_type}
        if include_size:
            out["size"] = self.size
        return out


@dataclass
class MessageSummary:
    id: str  # IMAP UID, as string for JSON portability
    date: str
    from_addr: str
    to_addrs: list[str]
    subject: str
    pec_type: str | None  # None for non-PEC mail, otherwise e.g. "accettazione"
    unread: bool
    has_attachments: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "from": self.from_addr,
            "to": self.to_addrs,
            "subject": self.subject,
            "pec_type": self.pec_type,
            "unread": self.unread,
            "has_attachments": self.has_attachments,
        }


@dataclass
class Message:
    id: str
    date: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str
    pec_type: str | None
    body_text: str
    body_html: str | None
    attachments: list[Attachment] = field(default_factory=list)
    daticert: DatiCert | None = None

    def to_dict(
        self,
        *,
        include_html: bool = False,
        include_cert: bool = False,
        include_cert_xml: bool = False,
    ) -> dict:
        out: dict = {
            "id": self.id,
            "date": self.date,
            "from": self.from_addr,
            "to": self.to_addrs,
            "cc": self.cc_addrs,
            "subject": self.subject,
            "pec_type": self.pec_type,
            "body": self.body_text,
        }
        if self.daticert is not None:
            out["pec_cert_type"] = self.daticert.tipo
        if include_html and self.body_html:
            out["body_html"] = self.body_html
        if include_cert and self.daticert is not None:
            out["pec_cert"] = self.daticert.to_dict()
        atts = [
            a.to_dict()
            for a in self.attachments
            if include_cert_xml or not _is_cert_attachment(a)
        ]
        if atts:
            out["attachments"] = atts
        return out


def _is_cert_attachment(att: Attachment) -> bool:
    """Filter out PEC certification XMLs in the default view.

    The PEC standard ships every message with `daticert.xml` (and sometimes
    `postacert.eml` for the original wrapped message). They're noise for a
    human reader; --verbose flips them back on.
    """
    name = (att.filename or "").lower()
    return name in {"daticert.xml", "postacert.eml", "smime.p7s", "smime.p7m"}
