"""Write cleaned messages and attachments to the output directory."""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from eml_parser import Attachment, ParsedEmail


@dataclass
class OutputConfig:
    output_dir: str = "./output"
    folder_template: str = "{year}/{date}_{subject}"
    max_subject_length: int = 80
    text_filename: str = "message.txt"
    attachments_subfolder: str = "attachments"
    encoding: str = "utf-8"


@dataclass
class AttachmentConfig:
    extract: bool = True
    min_size_bytes: int = 1024
    skip_signature_images: bool = True
    allowed_extensions: list[str] | None = None
    blocked_extensions: list[str] | None = None


def write_message(
    parsed: ParsedEmail,
    cleaned_body: str,
    out_cfg: OutputConfig,
    att_cfg: AttachmentConfig,
) -> Optional[str]:
    """Write a single processed message. Returns the output folder path or None if skipped."""
    folder_path = _build_folder_path(parsed, out_cfg)
    folder_path = _ensure_unique_folder(folder_path)
    folder_path.mkdir(parents=True, exist_ok=True)

    txt_path = folder_path / out_cfg.text_filename
    txt_path.write_text(
        _format_message_text(parsed, cleaned_body),
        encoding=out_cfg.encoding,
    )

    if att_cfg.extract:
        _write_attachments(parsed.attachments, folder_path, out_cfg, att_cfg)

    return str(folder_path)


def _build_folder_path(parsed: ParsedEmail, cfg: OutputConfig) -> Path:
    year = str(parsed.date.year) if parsed.date else "unknown"
    month = f"{parsed.date.month:02d}" if parsed.date else "00"
    day = f"{parsed.date.day:02d}" if parsed.date else "00"
    date_str = f"{year}-{month}-{day}" if parsed.date else "unknown-date"

    subject_slug = _slugify(parsed.subject, cfg.max_subject_length)
    from_slug = _slugify(parsed.from_addr, 40)
    folder_slug = _slugify(parsed.folder, 30)

    msg_id = re.sub(r"[^\w.-]", "_", parsed.message_id)[:40] if parsed.message_id else "no-id"

    template_vars = {
        "year": year,
        "month": month,
        "day": day,
        "date": date_str,
        "subject": subject_slug,
        "from": from_slug,
        "folder": folder_slug,
        "message_id": msg_id,
    }

    rel_path = cfg.folder_template.format(**template_vars)
    return Path(cfg.output_dir) / rel_path


def _format_message_text(parsed: ParsedEmail, cleaned_body: str) -> str:
    lines = [
        f"Subject: {parsed.subject}",
        f"From: {parsed.from_name} <{parsed.from_addr}>" if parsed.from_name else f"From: {parsed.from_addr}",
        f"To: {parsed.to_addrs}",
    ]
    if parsed.cc_addrs:
        lines.append(f"Cc: {parsed.cc_addrs}")

    date_display = parsed.date.strftime("%Y-%m-%d %H:%M") if parsed.date else parsed.date_str
    lines.append(f"Date: {date_display}")

    if parsed.folder:
        lines.append(f"Folder: {parsed.folder}")

    if parsed.importance and parsed.importance.lower() not in ("normal", "3"):
        lines.append(f"Importance: {parsed.importance}")

    if parsed.attachments:
        att_names = [a.filename for a in parsed.attachments]
        lines.append(f"Attachments: {', '.join(att_names)}")

    lines.append("")
    lines.append("─" * 72)
    lines.append("")
    lines.append(cleaned_body)
    lines.append("")

    return "\n".join(lines)


def _write_attachments(
    attachments: list[Attachment],
    folder: Path,
    out_cfg: OutputConfig,
    att_cfg: AttachmentConfig,
) -> None:
    if not attachments:
        return

    att_dir = folder / out_cfg.attachments_subfolder
    used_names: set[str] = set()

    for att in attachments:
        if not _should_keep_attachment(att, att_cfg):
            continue

        if att_dir is not None and not att_dir.exists():
            att_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _unique_filename(att.filename, used_names)
        used_names.add(safe_name.lower())

        (att_dir / safe_name).write_bytes(att.data)


def _should_keep_attachment(att: Attachment, cfg: AttachmentConfig) -> bool:
    if att.size < cfg.min_size_bytes:
        return False

    if cfg.skip_signature_images and att.is_inline:
        cid = (att.content_id or "").lower()
        if any(marker in cid for marker in ("image00", "logo", "banner", "signature")):
            return False
        if att.content_type.startswith("image/") and att.size < 20_000:
            return False

    ext = att.extension
    if cfg.blocked_extensions and ext in cfg.blocked_extensions:
        return False
    if cfg.allowed_extensions and ext not in cfg.allowed_extensions:
        return False

    return True


def _ensure_unique_folder(folder: Path) -> Path:
    """Append a numeric suffix if the folder already exists."""
    if not folder.exists():
        return folder
    counter = 2
    while True:
        candidate = folder.parent / f"{folder.name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def _slugify(text: str, max_len: int = 80) -> str:
    if not text:
        return "untitled"
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[-\s]+", "-", text).lower()
    return text[:max_len].rstrip("-") or "untitled"


def _unique_filename(name: str, used: set[str]) -> str:
    if name.lower() not in used:
        return name
    stem = Path(name).stem
    ext = Path(name).suffix
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{ext}"
        if candidate.lower() not in used:
            return candidate
        counter += 1


# ── CSV index ───────────────────────────────────────────────────────────────

class IndexWriter:
    FIELDNAMES = [
        "date", "from", "to", "cc", "subject", "folder",
        "importance", "attachments", "body_length", "output_path", "source_path",
    ]

    def __init__(self, output_dir: str):
        self._path = Path(output_dir) / "index.csv"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()

    def add(self, parsed: ParsedEmail, cleaned_body: str, output_path: str) -> None:
        self._writer.writerow({
            "date": parsed.date.isoformat() if parsed.date else "",
            "from": parsed.from_addr,
            "to": parsed.to_addrs,
            "cc": parsed.cc_addrs,
            "subject": parsed.subject,
            "folder": parsed.folder,
            "importance": parsed.importance,
            "attachments": "; ".join(a.filename for a in parsed.attachments),
            "body_length": len(cleaned_body),
            "output_path": output_path,
            "source_path": parsed.source_path,
        })

    def close(self) -> None:
        self._file.close()
