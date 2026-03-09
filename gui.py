"""EML-to-Files graphical interface."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from convert import run_conversion
from output_writer import AttachmentConfig
from text_cleaner import CleanerConfig

log = logging.getLogger("eml-to-files")


class _TextBoxHandler(logging.Handler):
    """Routes log records into a CTkTextbox (thread-safe via .after)."""

    def __init__(self, textbox: ctk.CTkTextbox) -> None:
        super().__init__()
        self._textbox = textbox

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        self._textbox.after(0, self._append, msg)

    def _append(self, msg: str) -> None:
        self._textbox.configure(state="normal")
        self._textbox.insert("end", msg)
        self._textbox.see("end")
        self._textbox.configure(state="disabled")


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("EML to Files")
        self.geometry("660x600")
        self.minsize(520, 480)

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self._running = False
        self._build_ui()
        self._setup_logging()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        heading = ctk.CTkLabel(
            self, text="EML to Files",
            font=ctk.CTkFont(size=26, weight="bold"),
        )
        heading.pack(pady=(24, 2))

        subtitle = ctk.CTkLabel(
            self,
            text="Convert .eml exports to clean text + attachments",
            text_color="gray",
        )
        subtitle.pack(pady=(0, 16))

        # -- folder pickers --
        folders = ctk.CTkFrame(self)
        folders.pack(fill="x", padx=24, pady=(0, 8))
        folders.columnconfigure(1, weight=1)

        ctk.CTkLabel(folders, text="Input folder") \
            .grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        self._input_var = ctk.StringVar()
        ctk.CTkEntry(folders, textvariable=self._input_var) \
            .grid(row=1, column=0, columnspan=2, sticky="ew", padx=(10, 5), pady=4)
        ctk.CTkButton(folders, text="Browse", width=80, command=self._browse_input) \
            .grid(row=1, column=2, padx=(0, 10), pady=4)

        ctk.CTkLabel(folders, text="Output folder") \
            .grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 0))
        self._output_var = ctk.StringVar()
        ctk.CTkEntry(folders, textvariable=self._output_var) \
            .grid(row=3, column=0, columnspan=2, sticky="ew", padx=(10, 5), pady=(4, 10))
        ctk.CTkButton(folders, text="Browse", width=80, command=self._browse_output) \
            .grid(row=3, column=2, padx=(0, 10), pady=(4, 10))

        # -- options --
        opts = ctk.CTkFrame(self)
        opts.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(opts, text="Options", font=ctk.CTkFont(weight="bold")) \
            .pack(anchor="w", padx=10, pady=(10, 4))

        self._extract_att = ctk.BooleanVar(value=True)
        self._strip_sig = ctk.BooleanVar(value=True)
        self._simplify = ctk.BooleanVar(value=True)
        self._write_idx = ctk.BooleanVar(value=True)

        for label, var in (
            ("Extract attachments", self._extract_att),
            ("Strip email signatures", self._strip_sig),
            ("Simplify reply threads", self._simplify),
            ("Write CSV index", self._write_idx),
        ):
            ctk.CTkCheckBox(opts, text=label, variable=var) \
                .pack(anchor="w", padx=24, pady=3)
        ctk.CTkFrame(opts, height=8, fg_color="transparent").pack()

        # -- convert button --
        self._btn = ctk.CTkButton(
            self, text="Convert", height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start,
        )
        self._btn.pack(fill="x", padx=24, pady=10)

        # -- progress --
        self._progress = ctk.CTkProgressBar(self)
        self._progress.pack(fill="x", padx=24, pady=(0, 4))
        self._progress.set(0)

        self._status = ctk.CTkLabel(self, text="Ready", anchor="w")
        self._status.pack(fill="x", padx=24)

        # -- log --
        self._log = ctk.CTkTextbox(self, height=120, state="disabled")
        self._log.pack(fill="both", expand=True, padx=24, pady=(4, 24))

    # ── helpers ─────────────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = _TextBoxHandler(self._log)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Select input folder with .eml files")
        if path:
            self._input_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self._output_var.set(path)

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── conversion ──────────────────────────────────────────────────────

    def _start(self) -> None:
        if self._running:
            return

        input_dir = self._input_var.get().strip()
        output_dir = self._output_var.get().strip()

        if not input_dir:
            messagebox.showwarning("Missing input", "Please select an input folder.")
            return
        if not Path(input_dir).is_dir():
            messagebox.showerror("Invalid path", f"Input folder does not exist:\n{input_dir}")
            return
        if not output_dir:
            messagebox.showwarning("Missing output", "Please select an output folder.")
            return

        self._running = True
        self._btn.configure(state="disabled", text="Converting\u2026")
        self._progress.set(0)
        self._clear_log()

        cleaner_cfg = CleanerConfig(
            strip_signatures=self._strip_sig.get(),
            simplify_threads=self._simplify.get(),
        )
        attach_cfg = AttachmentConfig(
            extract=self._extract_att.get(),
        )

        thread = threading.Thread(
            target=self._run,
            args=(input_dir, output_dir, cleaner_cfg, attach_cfg),
            daemon=True,
        )
        thread.start()

    def _run(
        self,
        input_dir: str,
        output_dir: str,
        cleaner_cfg: CleanerConfig,
        attach_cfg: AttachmentConfig,
    ) -> None:
        try:
            stats = run_conversion(
                input_dir=input_dir,
                output_dir=output_dir,
                cleaner_cfg=cleaner_cfg,
                attach_cfg=attach_cfg,
                write_index=self._write_idx.get(),
                progress_callback=self._on_progress,
            )
            self.after(0, self._on_done, stats, None)
        except Exception as exc:
            self.after(0, self._on_done, None, exc)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.after(0, self._progress.set, current / total)
        self.after(0, self._status.configure, {"text": f"[{current}/{total}] {message}"})

    def _on_done(self, stats: dict | None, error: Exception | None) -> None:
        self._running = False
        self._btn.configure(state="normal", text="Convert")

        if error:
            self._status.configure(text=f"Error: {error}")
            messagebox.showerror("Error", str(error))
        elif stats:
            summary = (
                f"Done in {stats['elapsed']:.1f}s \u2014 "
                f"{stats['ok']} converted, "
                f"{stats['skipped']} skipped, "
                f"{stats['errors']} errors"
            )
            self._status.configure(text=summary)
            self._progress.set(1.0)
            messagebox.showinfo("Complete", summary)
