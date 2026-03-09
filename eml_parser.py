"""Core EML parsing: extract headers, decoded body parts, and attachments."""

from __future__ import annotations

import email
import email.policy
import email.utils
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes
    content_id: Optional[str] = None
    is_inline: bool = False

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower() if self.filename else ""

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class ParsedEmail:
    source_path: str
    message_id: str = ""
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    to_addrs: str = ""
    cc_addrs: str = ""
    date: Optional[datetime] = None
    date_str: str = ""
    folder: str = ""
    plain_body: str = ""
    html_body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    thread_index: str = ""
    importance: str = ""


def parse_eml(filepath: str | Path) -> ParsedEmail:
    filepath = Path(filepath)
    raw = filepath.read_bytes()

    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)

    parsed = ParsedEmail(source_path=str(filepath))
    _extract_headers(msg, parsed)
    _extract_folder_from_path(filepath, parsed)
    _extract_body_and_attachments(msg, parsed)

    return parsed


def _extract_headers(msg: EmailMessage, parsed: ParsedEmail) -> None:
    parsed.message_id = msg.get("Message-ID", "").strip("<>")
    parsed.subject = _decode_header(msg.get("Subject", ""))
    parsed.importance = msg.get("Importance", "") or msg.get("X-Priority", "")

    from_header = msg.get("From", "")
    name, addr = email.utils.parseaddr(from_header)
    parsed.from_name = _decode_header(name)
    parsed.from_addr = addr

    parsed.to_addrs = _decode_header(msg.get("To", ""))
    parsed.cc_addrs = _decode_header(msg.get("Cc", ""))

    date_str = msg.get("Date", "")
    parsed.date_str = date_str
    try:
        parsed.date = email.utils.parsedate_to_datetime(date_str)
    except Exception:
        mailstore_date = msg.get("X-MailStore-Date", "")
        if mailstore_date:
            try:
                parsed.date = datetime.strptime(mailstore_date, "%Y%m%d%H%M%S")
            except ValueError:
                pass

    parsed.in_reply_to = msg.get("In-Reply-To", "").strip("<>")
    refs = msg.get("References", "")
    if refs:
        parsed.references = [r.strip("<>") for r in refs.split() if r.strip()]
    parsed.thread_index = msg.get("Thread-Index", "")


def _extract_folder_from_path(filepath: Path, parsed: ParsedEmail) -> None:
    """Derive the mailbox folder from the filesystem path."""
    parts = filepath.parts
    known_folders = {
        "inbox", "sent items", "gesendete objekte", "deleted items",
        "journal incoming", "journal outgoing", "drafts", "junk",
    }
    for part in reversed(parts):
        if part.lower() in known_folders:
            parsed.folder = part
            return
    parsed.folder = parts[-2] if len(parts) >= 2 else ""


def _extract_body_and_attachments(msg: EmailMessage, parsed: ParsedEmail) -> None:
    if not msg.is_multipart():
        ct = msg.get_content_type()
        payload = _safe_get_content(msg)
        if ct == "text/plain" and isinstance(payload, str):
            parsed.plain_body = payload
        elif ct == "text/html" and isinstance(payload, str):
            parsed.html_body = payload
        elif isinstance(payload, bytes):
            _maybe_attachment(msg, payload, parsed)
        return

    for part in msg.walk():
        ct = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()

        if ct == "multipart":
            continue

        payload = _safe_get_content(part)

        if disposition == "attachment" or (
            disposition != "inline" and ct not in ("text/plain", "text/html")
            and isinstance(payload, bytes)
        ):
            if isinstance(payload, bytes):
                _maybe_attachment(part, payload, parsed)
            continue

        if ct == "text/plain" and isinstance(payload, str):
            if not parsed.plain_body:
                parsed.plain_body = payload
        elif ct == "text/html" and isinstance(payload, str):
            if not parsed.html_body:
                parsed.html_body = payload
        elif isinstance(payload, bytes) and disposition == "inline":
            _maybe_attachment(part, payload, parsed)


def _maybe_attachment(part: EmailMessage, data: bytes, parsed: ParsedEmail) -> None:
    filename = part.get_filename()
    if not filename:
        ct = part.get_content_type()
        ext = _ext_from_content_type(ct)
        cid = (part.get("Content-ID") or "").strip("<>")
        filename = cid or f"unnamed{ext}"

    filename = _decode_header(filename)
    filename = _sanitize_filename(filename)

    att = Attachment(
        filename=filename,
        content_type=part.get_content_type(),
        data=data,
        content_id=(part.get("Content-ID") or "").strip("<>"),
        is_inline=(part.get_content_disposition() or "").lower() == "inline",
    )
    parsed.attachments.append(att)


def _safe_get_content(part: EmailMessage):
    try:
        return part.get_content()
    except Exception:
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                return ""
            charset = part.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace")
        except Exception:
            return ""


def _decode_header(value: str) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        decoded = []
        for part_bytes, charset in parts:
            if isinstance(part_bytes, bytes):
                charset = charset or "utf-8"
                try:
                    decoded.append(part_bytes.decode(charset, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    decoded.append(part_bytes.decode("utf-8", errors="replace"))
            else:
                decoded.append(part_bytes)
        return " ".join(decoded)
    except Exception:
        return str(value)


_EXT_MAP = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "text/csv": ".csv",
}


def _ext_from_content_type(ct: str) -> str:
    return _EXT_MAP.get(ct, ".bin")


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    name = _UNSAFE_CHARS.sub("_", name)
    return name.strip(". ") or "unnamed"
