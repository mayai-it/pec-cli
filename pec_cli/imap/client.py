"""IMAP client for PEC mailboxes (stdlib imaplib + email).

Folders are provider-dependent but all major Italian PEC providers expose:
- INBOX                — incoming
- INBOX.Sent / Sent    — sent items (Aruba uses INBOX.Sent)

We probe a small set of candidates when the user asks for "sent" so the same
CLI works across providers without configuration.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import ssl
import sys
import time
from collections.abc import Iterable
from email.header import decode_header, make_header
from email.message import Message as EmailMessage

from pec_cli.auth import Credentials
from pec_cli.daticert import DatiCert, parse_daticert
from pec_cli.models import Attachment, Message, MessageSummary


class IMAPError(Exception):
    """Raised on IMAP protocol or login failures."""


_SENT_FOLDER_CANDIDATES = ("INBOX.Sent", "Sent", "INBOX/Sent", "Posta inviata")


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.decode("latin-1", errors="replace")
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_addr_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _, addr in email.utils.getaddresses([_decode(value)]) if addr]


def _format_date(raw: str | None) -> str:
    """Normalize the Date header to ISO 8601, falling back to the original."""
    if not raw:
        return ""
    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed is None:
        return _decode(raw)
    return parsed.isoformat()


def _pec_type(msg: EmailMessage) -> str | None:
    """PEC providers set `X-Trasporto` / `X-Ricevuta` on certification mails."""
    rec = msg.get("X-Ricevuta")
    if rec:
        return rec.strip().lower()
    trasp = msg.get("X-Trasporto")
    if trasp:
        return trasp.strip().lower()
    return None


def _walk_attachments(msg: EmailMessage, *, load_bytes: bool) -> list[Attachment]:
    out: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if not filename and "attachment" not in disp:
            continue
        if not filename:
            continue
        filename = _decode(filename)
        payload = part.get_payload(decode=True) or b""
        out.append(Attachment(
            filename=filename,
            content_type=part.get_content_type(),
            size=len(payload),
            data=payload if load_bytes else None,
        ))
    return out


def _extract_bodies(msg: EmailMessage) -> tuple[str, str | None]:
    """Return (text, html) bodies, preferring the first text/* parts found."""
    text_body = ""
    html_body: str | None = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and not text_body:
                text_body = _decode_part(part)
            elif ctype == "text/html" and html_body is None:
                html_body = _decode_part(part)
    else:
        if msg.get_content_type() == "text/html":
            html_body = _decode_part(msg)
        else:
            text_body = _decode_part(msg)
    return text_body, html_body


def _decode_part(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


class IMAPClient:
    """Thin wrapper around imaplib with PEC-aware helpers."""

    def __init__(self, creds: Credentials, *, verbose: bool = False) -> None:
        self.creds = creds
        self.verbose = verbose
        self._imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> IMAPClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        pc = self.creds.provider_config
        t0 = time.monotonic()
        try:
            self._imap = imaplib.IMAP4_SSL(
                pc.imap_host, pc.imap_port, ssl_context=ssl.create_default_context()
            )
            self._imap.login(self.creds.address, self.creds.password)
        except imaplib.IMAP4.error as exc:
            raise IMAPError(f"IMAP login failed: {exc}") from exc
        except OSError as exc:
            raise IMAPError(f"could not reach {pc.imap_host}:{pc.imap_port}: {exc}") from exc
        if self.verbose:
            elapsed = (time.monotonic() - t0) * 1000
            sys.stderr.write(
                f"imap: connected to {pc.imap_host}:{pc.imap_port} as {self.creds.address} "
                f"({elapsed:.0f}ms)\n"
            )

    def close(self) -> None:
        if self._imap is None:
            return
        try:
            self._imap.logout()
        except Exception:
            pass
        self._imap = None

    # ------------------------------------------------------------------
    # Folder selection
    # ------------------------------------------------------------------

    def _imap_or_raise(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise IMAPError("not connected — call connect() first")
        return self._imap

    def select_folder(self, alias: str, *, readonly: bool = True) -> str:
        """Select an IMAP folder by alias (`inbox`, `sent`) or raw name.

        Returns the actual folder name selected so callers can log it.
        """
        imap = self._imap_or_raise()
        alias_lc = alias.lower()
        candidates: Iterable[str]
        if alias_lc == "inbox":
            candidates = ("INBOX",)
        elif alias_lc == "sent":
            candidates = _SENT_FOLDER_CANDIDATES
        else:
            candidates = (alias,)
        last_err = None
        for name in candidates:
            typ, _ = imap.select(name, readonly=readonly)
            if typ == "OK":
                if self.verbose:
                    sys.stderr.write(f"imap: selected folder {name!r}\n")
                return name
            last_err = name
        raise IMAPError(f"could not select folder (tried {last_err!r})")

    # ------------------------------------------------------------------
    # Searching / listing
    # ------------------------------------------------------------------

    def search(self, *, unread: bool = False, since: str | None = None) -> list[str]:
        """Return UIDs (as strings) matching the filters, newest last.

        `since` is YYYY-MM-DD; converted to IMAP's `DD-Mon-YYYY` form.
        """
        imap = self._imap_or_raise()
        criteria: list[str] = []
        if unread:
            criteria.append("UNSEEN")
        if since:
            criteria.append(f"SINCE {_imap_date(since)}")
        if not criteria:
            criteria.append("ALL")
        typ, data = imap.uid("search", None, *criteria)
        if typ != "OK":
            raise IMAPError(f"IMAP search failed: {data!r}")
        uids = (data[0] or b"").split()
        return [u.decode("ascii") for u in uids]

    def fetch_summaries(self, uids: list[str]) -> list[MessageSummary]:
        """Fetch lightweight summaries for a batch of UIDs.

        Uses BODY.PEEK[HEADER.FIELDS (...)] to avoid marking messages as read,
        plus FLAGS and BODYSTRUCTURE to determine read state and attachments.
        """
        if not uids:
            return []
        imap = self._imap_or_raise()
        uid_set = ",".join(uids)
        typ, raw = imap.uid(
            "fetch",
            uid_set,
            "(FLAGS BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS "
            "(DATE FROM TO CC SUBJECT X-RICEVUTA X-TRASPORTO)])",
        )
        if typ != "OK":
            raise IMAPError(f"IMAP fetch failed: {raw!r}")
        return _parse_summary_response(raw)

    def fetch_message(self, uid: str) -> Message:
        """Fetch a full message and parse it into a Message dataclass."""
        imap = self._imap_or_raise()
        typ, raw = imap.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not raw or raw[0] is None:
            raise IMAPError(f"could not fetch message {uid}")
        # imaplib returns [(b'UID ... {n}', b'<raw bytes>'), b')'] — find tuple
        raw_bytes = b""
        for item in raw:
            if isinstance(item, tuple) and len(item) >= 2:
                raw_bytes = item[1]
                break
        if not raw_bytes:
            raise IMAPError(f"empty body for message {uid}")
        parsed = email.message_from_bytes(raw_bytes)

        text, html = _extract_bodies(parsed)
        attachments = _walk_attachments(parsed, load_bytes=True)
        daticert = _find_daticert(attachments)

        return Message(
            id=str(uid),
            date=_format_date(parsed.get("Date")),
            from_addr=_first_addr(parsed.get("From")),
            to_addrs=_parse_addr_list(parsed.get("To")),
            cc_addrs=_parse_addr_list(parsed.get("Cc")),
            subject=_decode(parsed.get("Subject")),
            pec_type=_pec_type(parsed),
            body_text=text,
            body_html=html,
            attachments=attachments,
            daticert=daticert,
        )


def _find_daticert(attachments: list[Attachment]) -> DatiCert | None:
    for att in attachments:
        if (att.filename or "").lower() == "daticert.xml" and att.data:
            return parse_daticert(att.data)
    return None


# ---------------------------------------------------------------------------
# Response parsing helpers (module-level for testability)
# ---------------------------------------------------------------------------


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(yyyy_mm_dd: str) -> str:
    """Convert YYYY-MM-DD to IMAP's DD-Mon-YYYY (e.g. 01-Jan-2025)."""
    parts = yyyy_mm_dd.split("-")
    if len(parts) != 3:
        raise IMAPError(f"invalid date {yyyy_mm_dd!r}, expected YYYY-MM-DD")
    y, m, d = parts
    try:
        month = _MONTHS[int(m) - 1]
    except (ValueError, IndexError) as exc:
        raise IMAPError(f"invalid month in {yyyy_mm_dd!r}") from exc
    return f"{int(d):02d}-{month}-{int(y):04d}"


def _first_addr(value: str | None) -> str:
    addrs = _parse_addr_list(value)
    return addrs[0] if addrs else _decode(value)


def _parse_summary_response(raw: list) -> list[MessageSummary]:
    """Walk imaplib's quirky FETCH response and build MessageSummary objects.

    imaplib alternates tuples and bytes; each message appears as:
        (b'<n> (UID <uid> FLAGS (...) BODYSTRUCTURE (...) BODY[HEADER...] {N}',
         b'<headers raw>')
        b')'
    """
    summaries: list[MessageSummary] = []
    for item in raw:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, headers_blob = item[0], item[1]
        if not isinstance(meta, bytes) or not isinstance(headers_blob, bytes):
            continue
        meta_s = meta.decode("ascii", errors="replace")

        uid = _extract_token(meta_s, "UID")
        flags = _extract_parens(meta_s, "FLAGS")
        bodystructure = _extract_parens(meta_s, "BODYSTRUCTURE")

        unread = "\\Seen" not in (flags or "")
        has_attachments = _bodystructure_has_attachments(bodystructure or "")

        parsed = email.message_from_bytes(headers_blob)
        summaries.append(MessageSummary(
            id=uid or "",
            date=_format_date(parsed.get("Date")),
            from_addr=_first_addr(parsed.get("From")),
            to_addrs=_parse_addr_list(parsed.get("To")),
            subject=_decode(parsed.get("Subject")),
            pec_type=_pec_type(parsed),
            unread=unread,
            has_attachments=has_attachments,
        ))
    # Newest first
    summaries.sort(key=lambda s: s.date, reverse=True)
    return summaries


def _extract_token(s: str, key: str) -> str | None:
    """Pull out a single-token value after `KEY ` in an IMAP response line."""
    needle = f"{key} "
    idx = s.find(needle)
    if idx < 0:
        return None
    rest = s[idx + len(needle):].lstrip()
    # token ends at whitespace or ')' or '('
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in " ()":
            end = i
            break
    return rest[:end] or None


def _extract_parens(s: str, key: str) -> str | None:
    """Pull out the parenthesized value after KEY in an IMAP response line."""
    needle = f"{key} ("
    idx = s.find(needle)
    if idx < 0:
        return None
    depth = 0
    start = idx + len(needle) - 1
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[start + 1:i]
    return None


def _bodystructure_has_attachments(bs: str) -> bool:
    """Cheap heuristic: a BODYSTRUCTURE with `"attachment"` (case-insensitive)
    or multiple MIME parts is treated as having attachments.

    PEC messages are almost always multipart (text + daticert.xml at minimum),
    so we look specifically for the `"attachment"` disposition string.
    """
    return '"attachment"' in bs.lower()
