#!/usr/bin/env python3
"""
EML-to-Files: Convert a folder of .eml files into clean text + attachments.

CLI usage:
    python convert.py                     # uses config.yaml in current dir
    python convert.py -c my_config.yaml   # custom config file
    python convert.py --dry-run           # preview without writing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from eml_parser import ParsedEmail, parse_eml
from output_writer import AttachmentConfig, IndexWriter, OutputConfig, write_message
from text_cleaner import CleanerConfig, clean_body

log = logging.getLogger("eml-to-files")


# ── Public API ──────────────────────────────────────────────────────────────

def run_conversion(
    input_dir: str | Path,
    output_dir: str | Path = "./output",
    cleaner_cfg: CleanerConfig | None = None,
    output_cfg: OutputConfig | None = None,
    attach_cfg: AttachmentConfig | None = None,
    workers: int = 0,
    dry_run: bool = False,
    write_index: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """
    Run the EML-to-files conversion.

    Args:
        input_dir: Folder containing .eml files (searched recursively).
        output_dir: Destination folder for converted output.
        cleaner_cfg: Text cleaning settings (sensible defaults if None).
        output_cfg: Output layout settings (sensible defaults if None).
        attach_cfg: Attachment extraction settings (sensible defaults if None).
        workers: Parallel workers (0 = CPU count).
        dry_run: Preview without writing anything.
        write_index: Write an index.csv of all processed messages.
        progress_callback: Called as callback(current, total, message).

    Returns:
        dict with keys: ok, skipped, errors, total, elapsed
    """
    input_dir = Path(input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    eml_files = sorted(input_dir.rglob("*.eml"))
    total = len(eml_files)

    if not eml_files:
        log.warning("No .eml files found in %s", input_dir)
        if progress_callback:
            progress_callback(0, 0, "No .eml files found.")
        return {"ok": 0, "skipped": 0, "errors": 0, "total": 0, "elapsed": 0.0}

    cleaner_cfg = cleaner_cfg or CleanerConfig()
    output_cfg = output_cfg or OutputConfig()
    output_cfg.output_dir = str(output_dir)
    attach_cfg = attach_cfg or AttachmentConfig()

    workers = workers or os.cpu_count() or 4
    index_writer = IndexWriter(str(output_dir)) if (write_index and not dry_run) else None

    log.info("Found %d .eml files in %s", total, input_dir)
    if progress_callback:
        progress_callback(0, total, f"Found {total} .eml files")

    t0 = time.perf_counter()
    stats = {"ok": 0, "skipped": 0, "errors": 0}
    done = 0

    if workers == 1:
        for fp in eml_files:
            _process_one(fp, cleaner_cfg, output_cfg, attach_cfg, dry_run, index_writer, stats)
            done += 1
            if progress_callback:
                progress_callback(done, total, fp.name)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(eml_files))) as pool:
            futures = {
                pool.submit(_parse_and_clean, fp, cleaner_cfg): fp
                for fp in eml_files
            }
            for fut in as_completed(futures):
                fp = futures[fut]
                try:
                    parsed, cleaned = fut.result()
                except Exception:
                    log.exception("Failed to process %s", fp)
                    stats["errors"] += 1
                    done += 1
                    if progress_callback:
                        progress_callback(done, total, f"Error: {fp.name}")
                    continue

                if not cleaned or len(cleaned) < cleaner_cfg.min_body_length:
                    log.debug("Skipped (too short): %s", fp.name)
                    stats["skipped"] += 1
                    done += 1
                    if progress_callback:
                        progress_callback(done, total, f"Skipped: {fp.name}")
                    continue

                if dry_run:
                    log.info("[DRY RUN] Would write: %s", fp.name)
                    stats["ok"] += 1
                    done += 1
                    if progress_callback:
                        progress_callback(done, total, f"[DRY RUN] {fp.name}")
                    continue

                try:
                    out_path = write_message(parsed, cleaned, output_cfg, attach_cfg)
                    if index_writer:
                        index_writer.add(parsed, cleaned, out_path or "")
                    stats["ok"] += 1
                except Exception:
                    log.exception("Failed to write output for %s", fp)
                    stats["errors"] += 1

                done += 1
                if progress_callback:
                    progress_callback(done, total, fp.name)

    if index_writer:
        index_writer.close()

    elapsed = time.perf_counter() - t0
    log.info(
        "Done in %.1fs — %d processed, %d skipped, %d errors",
        elapsed, stats["ok"], stats["skipped"], stats["errors"],
    )
    return {**stats, "total": total, "elapsed": elapsed}


# ── Internals ───────────────────────────────────────────────────────────────

def _process_one(
    fp: Path,
    cleaner_cfg: CleanerConfig,
    output_cfg: OutputConfig,
    attach_cfg: AttachmentConfig,
    dry_run: bool,
    index_writer: IndexWriter | None,
    stats: dict,
) -> None:
    try:
        parsed, cleaned = _parse_and_clean(fp, cleaner_cfg)
    except Exception:
        log.exception("Failed to parse %s", fp)
        stats["errors"] += 1
        return

    if not cleaned or len(cleaned) < cleaner_cfg.min_body_length:
        log.debug("Skipped (too short): %s", fp.name)
        stats["skipped"] += 1
        return

    if dry_run:
        log.info("[DRY RUN] Would write: %s", fp.name)
        stats["ok"] += 1
        return

    try:
        out_path = write_message(parsed, cleaned, output_cfg, attach_cfg)
        if index_writer:
            index_writer.add(parsed, cleaned, out_path or "")
        stats["ok"] += 1
        log.debug("Wrote: %s", out_path)
    except Exception:
        log.exception("Failed to write output for %s", fp)
        stats["errors"] += 1


def _parse_and_clean(fp: Path, cleaner_cfg: CleanerConfig) -> tuple[ParsedEmail, str]:
    parsed = parse_eml(fp)
    cleaned = clean_body(parsed.plain_body, parsed.html_body, cleaner_cfg)
    return parsed, cleaned


# ── Config helpers ──────────────────────────────────────────────────────────

def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        log.warning("Config file not found at %s — using defaults", p)
        return {}
    if yaml is None:
        log.warning("PyYAML not installed — ignoring config file, using defaults")
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_cleaner_config(cfg: dict) -> CleanerConfig:
    f = cfg.get("filters", {})
    return CleanerConfig(
        strip_signatures=f.get("strip_signatures", True),
        extra_signature_markers=f.get("extra_signature_markers"),
        simplify_threads=f.get("simplify_threads", True),
        deduplicate_quotes=f.get("deduplicate_quotes", True),
        prefer_plaintext=f.get("prefer_plaintext", True),
        strip_disclaimers=f.get("strip_disclaimers", True),
        strip_cid_references=f.get("strip_cid_references", True),
        min_body_length=f.get("min_body_length", 10),
    )


def _build_output_config(cfg: dict) -> OutputConfig:
    o = cfg.get("output", {})
    return OutputConfig(
        output_dir=cfg.get("output_dir", "./output"),
        folder_template=o.get("folder_template", "{year}/{date}_{subject}"),
        max_subject_length=o.get("max_subject_length", 80),
        text_filename=o.get("text_filename", "message.txt"),
        attachments_subfolder=o.get("attachments_subfolder", "attachments"),
        encoding=o.get("encoding", "utf-8"),
    )


def _build_attachment_config(cfg: dict) -> AttachmentConfig:
    a = cfg.get("attachments", {})
    return AttachmentConfig(
        extract=a.get("extract", True),
        min_size_bytes=a.get("min_size_bytes", 1024),
        skip_signature_images=a.get("skip_signature_images", True),
        allowed_extensions=a.get("allowed_extensions") or None,
        blocked_extensions=a.get("blocked_extensions") or None,
    )


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    cfg = _load_config(args.config)

    log_level = cfg.get("log_level", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    workers = args.workers if args.workers is not None else cfg.get("workers", 0)

    try:
        run_conversion(
            input_dir=cfg.get("input_dir", "./input"),
            output_dir=cfg.get("output_dir", "./output"),
            cleaner_cfg=_build_cleaner_config(cfg),
            output_cfg=_build_output_config(cfg),
            attach_cfg=_build_attachment_config(cfg),
            workers=workers,
            dry_run=args.dry_run,
            write_index=cfg.get("write_index", True),
        )
    except FileNotFoundError as e:
        log.error(str(e))
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert .eml files to clean text + attachments")
    p.add_argument("-c", "--config", default="config.yaml", help="Path to YAML config file")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    p.add_argument("--workers", type=int, default=None, help="Override number of workers")
    return p.parse_args()


if __name__ == "__main__":
    main()
