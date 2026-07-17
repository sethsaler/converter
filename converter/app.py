"""Polished drag-and-drop file format converter GUI."""

from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from converter.engine import (
    COMPRESS_PRESETS,
    FORMAT_LABELS,
    HEIF_SUPPORTED,
    OUTPUT_FORMATS,
    common_formats,
    compress_file,
    convert_file,
    detect_kind,
    has_ffmpeg,
)
from converter.estimate import (
    SizeEstimate,
    estimate_batch,
    format_bytes,
)
from converter.guesser import FormatGuesser

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except ImportError:
    HAS_DND = False
    DND_FILES = None  # type: ignore[misc, assignment]
    TkinterDnD = None  # type: ignore[misc, assignment]


# ── Design tokens (dark, restrained) ────────────────────────────────────────
BG = "#0c0c0f"
SURFACE = "#141418"
SURFACE_RAISED = "#1a1a20"
SURFACE_HOVER = "#22222a"
BORDER = "#2a2a34"
BORDER_SOFT = "#1e1e26"
TEXT = "#f0f0f4"
TEXT_SECONDARY = "#a0a0b0"
TEXT_MUTED = "#6a6a7a"
ACCENT = "#5b8def"
ACCENT_SOFT = "#1a2744"
ACCENT_TEXT = "#8eb6ff"
SUCCESS = "#3dd68c"
SUCCESS_SOFT = "#0f2a1c"
ERROR = "#f07178"
ERROR_SOFT = "#2a1215"
WARN = "#e0af68"
CHIP = "#1e1e28"
CHIP_ACTIVE = "#5b8def"
CHIP_TEXT = "#c8c8d4"
CHIP_ACTIVE_TEXT = "#0c0c0f"

FONT_UI = ("SF Pro Text", 13)
FONT_UI_SM = ("SF Pro Text", 12)
FONT_UI_XS = ("SF Pro Text", 11)
FONT_UI_BOLD = ("SF Pro Text", 13, "bold")
FONT_TITLE = ("SF Pro Display", 26, "bold")
FONT_HEADING = ("SF Pro Display", 15, "bold")
FONT_MONO = ("SF Mono", 11)
FONT_DROP = ("SF Pro Display", 16)

KIND_ICON = {"image": "◈", "video": "▶", "audio": "♪", None: "•"}
KIND_COLOR = {
    "image": "#7aa2f7",
    "video": "#bb9af7",
    "audio": "#7dcfff",
}


def parse_dropped_paths(data: str) -> list[Path]:
    paths: list[Path] = []
    token = ""
    in_brace = False
    for ch in data:
        if ch == "{":
            in_brace = True
            token = ""
        elif ch == "}":
            in_brace = False
            if token:
                paths.append(Path(token))
            token = ""
        elif ch == " " and not in_brace:
            if token:
                paths.append(Path(token))
            token = ""
        else:
            token += ch
    if token:
        paths.append(Path(token))
    return [p for p in paths if p.exists()]


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


class Chip(tk.Frame):
    """Selectable format chip button."""

    def __init__(
        self,
        master,
        key: str,
        label: str,
        command=None,
        **kwargs,
    ):
        super().__init__(
            master,
            bg=CHIP,
            highlightthickness=1,
            highlightbackground=BORDER,
            cursor="hand2",
            **kwargs,
        )
        self.key = key
        self._command = command
        self._active = False
        self.label = tk.Label(
            self,
            text=label,
            bg=CHIP,
            fg=CHIP_TEXT,
            font=FONT_UI_SM,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.label.pack()
        for w in (self, self.label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _click(self, _e=None):
        if self._command:
            self._command(self.key)

    def _enter(self, _e=None):
        if not self._active:
            self.configure(bg=SURFACE_HOVER, highlightbackground=TEXT_MUTED)
            self.label.configure(bg=SURFACE_HOVER)

    def _leave(self, _e=None):
        if not self._active:
            self.configure(bg=CHIP, highlightbackground=BORDER)
            self.label.configure(bg=CHIP)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.configure(bg=CHIP_ACTIVE, highlightbackground=CHIP_ACTIVE)
            self.label.configure(bg=CHIP_ACTIVE, fg=CHIP_ACTIVE_TEXT, font=FONT_UI_BOLD)
        else:
            self.configure(bg=CHIP, highlightbackground=BORDER)
            self.label.configure(bg=CHIP, fg=CHIP_TEXT, font=FONT_UI_SM)


class FileRow(tk.Frame):
    """A single file entry in the queue."""

    def __init__(self, master, path: Path, on_remove, **kwargs):
        super().__init__(master, bg=SURFACE, **kwargs)
        self.path = path
        self._on_remove = on_remove
        self.status = "pending"  # pending | running | ok | err
        self._src_size = format_size(path.stat().st_size) if path.exists() else "?"

        kind = detect_kind(path)
        icon = KIND_ICON.get(kind, "•")
        color = KIND_COLOR.get(kind, TEXT_MUTED)

        self.configure(highlightthickness=1, highlightbackground=BORDER_SOFT)

        inner = tk.Frame(self, bg=SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        self.icon_lbl = tk.Label(
            inner, text=icon, bg=SURFACE, fg=color, font=("SF Pro Text", 16), width=2
        )
        self.icon_lbl.pack(side="left", padx=(0, 10))

        text_col = tk.Frame(inner, bg=SURFACE)
        text_col.pack(side="left", fill="x", expand=True)

        self.name_lbl = tk.Label(
            text_col,
            text=path.name,
            bg=SURFACE,
            fg=TEXT,
            font=FONT_UI,
            anchor="w",
        )
        self.name_lbl.pack(fill="x")

        src = path.suffix.upper().lstrip(".") or "?"
        meta = f"{src}  ·  {self._src_size}  ·  {kind or 'file'}"
        self.meta_lbl = tk.Label(
            text_col, text=meta, bg=SURFACE, fg=TEXT_MUTED, font=FONT_UI_XS, anchor="w"
        )
        self.meta_lbl.pack(fill="x", pady=(2, 0))

        # Right side: estimate (above) + status
        right = tk.Frame(inner, bg=SURFACE)
        right.pack(side="right", padx=(8, 0))

        self.remove_btn = tk.Label(
            right,
            text="✕",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_UI_SM,
            cursor="hand2",
            padx=6,
        )
        self.remove_btn.pack(side="right")
        self.remove_btn.bind("<Button-1>", lambda _e: self._on_remove(self.path))
        self.remove_btn.bind("<Enter>", lambda _e: self.remove_btn.configure(fg=ERROR))
        self.remove_btn.bind("<Leave>", lambda _e: self.remove_btn.configure(fg=TEXT_MUTED))

        status_col = tk.Frame(right, bg=SURFACE)
        status_col.pack(side="right", padx=(4, 8))

        self.estimate_lbl = tk.Label(
            status_col,
            text="",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
            anchor="e",
        )
        self.estimate_lbl.pack(anchor="e")

        self.status_lbl = tk.Label(
            status_col,
            text="",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
            anchor="e",
        )
        self.status_lbl.pack(anchor="e")

    def set_estimate(self, est: SizeEstimate | None) -> None:
        if self.status in ("running", "ok", "err"):
            return
        if est is None:
            self.estimate_lbl.configure(text="", fg=TEXT_MUTED)
            return
        pct = est.pct_change
        out = format_bytes(est.estimated_bytes)
        if abs(pct) < 3:
            text = f"≈ {out}"
            fg = TEXT_MUTED
        elif pct < 0:
            text = f"≈ {out}  (−{abs(pct):.0f}%)"
            fg = SUCCESS
        else:
            text = f"≈ {out}  (+{pct:.0f}%)"
            fg = WARN if pct < 40 else ERROR
        self.estimate_lbl.configure(text=text, fg=fg)

    def set_status(self, status: str, detail: str = "") -> None:
        self.status = status
        colors = {
            "pending": (TEXT_MUTED, ""),
            "running": (ACCENT_TEXT, "…"),
            "ok": (SUCCESS, "✓ done"),
            "err": (ERROR, "✗ fail"),
        }
        fg, text = colors.get(status, (TEXT_MUTED, detail))
        self.status_lbl.configure(text=detail or text, fg=fg)
        if status == "running":
            self.estimate_lbl.configure(text="", fg=TEXT_MUTED)
        if status == "ok":
            self.configure(highlightbackground=SUCCESS_SOFT)
        elif status == "err":
            self.configure(highlightbackground=ERROR_SOFT)
        elif status == "running":
            self.configure(highlightbackground=ACCENT)
        else:
            self.configure(highlightbackground=BORDER_SOFT)


class ConverterApp:
    def __init__(self) -> None:
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("Converter")
        self.root.geometry("820x700")
        self.root.minsize(640, 560)
        self.root.configure(bg=BG)

        # Subtle macOS vibrancy feel via pure dark canvas
        try:
            self.root.tk.call("tk::unsupported::MacWindowStyle", "style", self.root._w, "document")
        except tk.TclError:
            pass

        self.files: list[Path] = []
        self.file_rows: dict[str, FileRow] = {}
        self.output_dir: Path | None = None
        self._busy = False
        self._selected_format: str | None = None
        self._format_override = False  # user manually picked
        self._chips: dict[str, Chip] = {}
        self._last_outputs: list[Path] = []
        self._mode = "convert"  # convert | compress
        self._compress_preset = "balanced"
        self._preset_chips: dict[str, Chip] = {}
        self._estimate_job = 0  # debounce token
        self.guesser = FormatGuesser()

        self._build_ui()
        self._wire_dnd()
        self._on_quality_change()
        self._show_empty_state()

    # ── Layout ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # Outer padding shell
        self.shell = tk.Frame(self.root, bg=BG)
        self.shell.pack(fill="both", expand=True, padx=28, pady=24)

        # Header
        header = tk.Frame(self.shell, bg=BG)
        header.pack(fill="x", pady=(0, 18))

        title_col = tk.Frame(header, bg=BG)
        title_col.pack(side="left")
        tk.Label(
            title_col, text="Converter", bg=BG, fg=TEXT, font=FONT_TITLE
        ).pack(anchor="w")
        self.subtitle = tk.Label(
            title_col,
            text="Drop files · convert or compress",
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_SM,
        )
        self.subtitle.pack(anchor="w", pady=(2, 0))

        # Deps pills
        pills = tk.Frame(header, bg=BG)
        pills.pack(side="right")
        self.pill_ffmpeg = self._make_pill(pills, "ffmpeg")
        self.pill_heic = self._make_pill(pills, "HEIC")
        self.pill_drop = self._make_pill(pills, "drop")
        self._refresh_pills()

        # Mode switch (Convert / Compress) — always visible under header
        mode_row = tk.Frame(self.shell, bg=BG)
        mode_row.pack(fill="x", pady=(0, 14))
        self.mode_convert_btn = tk.Label(
            mode_row,
            text="Convert",
            bg=ACCENT,
            fg=CHIP_ACTIVE_TEXT,
            font=FONT_UI_BOLD,
            padx=16,
            pady=7,
            cursor="hand2",
        )
        self.mode_convert_btn.pack(side="left")
        self.mode_convert_btn.bind("<Button-1>", lambda _e: self._set_mode("convert"))
        self.mode_compress_btn = tk.Label(
            mode_row,
            text="Compress",
            bg=SURFACE,
            fg=TEXT_SECONDARY,
            font=FONT_UI_SM,
            padx=16,
            pady=7,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.mode_compress_btn.pack(side="left", padx=(6, 0))
        self.mode_compress_btn.bind("<Button-1>", lambda _e: self._set_mode("compress"))
        self.mode_hint = tk.Label(
            mode_row,
            text="Change format  ·  quality preserved at 100%",
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
        )
        self.mode_hint.pack(side="left", padx=(14, 0))

        # Drop / empty state OR file list live in content
        self.content = tk.Frame(self.shell, bg=BG)
        self.content.pack(fill="both", expand=True)

        # Empty / drop zone
        self.empty_state = tk.Frame(self.content, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        self.drop_inner = tk.Frame(self.empty_state, bg=SURFACE)
        self.drop_inner.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            self.drop_inner,
            text="↓",
            bg=SURFACE,
            fg=ACCENT,
            font=("SF Pro Display", 36),
        ).pack()
        tk.Label(
            self.drop_inner,
            text="Drop files here",
            bg=SURFACE,
            fg=TEXT,
            font=FONT_DROP,
        ).pack(pady=(8, 4))
        tk.Label(
            self.drop_inner,
            text="Images · Video · Audio    ·    or click to browse",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_UI_SM,
        ).pack()
        browse_link = tk.Label(
            self.drop_inner,
            text="Browse files",
            bg=SURFACE,
            fg=ACCENT_TEXT,
            font=FONT_UI_BOLD,
            cursor="hand2",
        )
        browse_link.pack(pady=(16, 0))
        browse_link.bind("<Button-1>", lambda _e: self.browse_files())
        browse_link.bind("<Enter>", lambda _e: browse_link.configure(fg=ACCENT))
        browse_link.bind("<Leave>", lambda _e: browse_link.configure(fg=ACCENT_TEXT))

        for w in (self.empty_state, self.drop_inner):
            w.bind("<Button-1>", lambda _e: self.browse_files())

        # Files view (hidden until files added)
        self.files_view = tk.Frame(self.content, bg=BG)

        # Suggestion banner
        self.suggest_banner = tk.Frame(
            self.files_view, bg=ACCENT_SOFT, highlightthickness=1, highlightbackground=ACCENT
        )
        self.suggest_banner.pack(fill="x", pady=(0, 12))
        ban_inner = tk.Frame(self.suggest_banner, bg=ACCENT_SOFT)
        ban_inner.pack(fill="x", padx=14, pady=12)

        left_ban = tk.Frame(ban_inner, bg=ACCENT_SOFT)
        left_ban.pack(side="left", fill="x", expand=True)
        tk.Label(
            left_ban,
            text="Suggested",
            bg=ACCENT_SOFT,
            fg=ACCENT_TEXT,
            font=FONT_UI_XS,
        ).pack(anchor="w")
        self.suggest_title = tk.Label(
            left_ban,
            text="—",
            bg=ACCENT_SOFT,
            fg=TEXT,
            font=FONT_HEADING,
            anchor="w",
        )
        self.suggest_title.pack(anchor="w", pady=(2, 0))
        self.suggest_reason = tk.Label(
            left_ban,
            text="",
            bg=ACCENT_SOFT,
            fg=TEXT_SECONDARY,
            font=FONT_UI_SM,
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self.suggest_reason.pack(anchor="w", pady=(2, 0))

        self.use_suggest_btn = tk.Label(
            ban_inner,
            text="Use suggestion",
            bg=ACCENT,
            fg=CHIP_ACTIVE_TEXT,
            font=FONT_UI_BOLD,
            padx=14,
            pady=8,
            cursor="hand2",
        )
        self.use_suggest_btn.pack(side="right", padx=(12, 0))
        self.use_suggest_btn.bind("<Button-1>", lambda _e: self._apply_suggestion())
        self.use_suggest_btn.bind(
            "<Enter>", lambda _e: self.use_suggest_btn.configure(bg=ACCENT_TEXT)
        )
        self.use_suggest_btn.bind(
            "<Leave>", lambda _e: self.use_suggest_btn.configure(bg=ACCENT)
        )

        # File list header
        self.list_header = tk.Frame(self.files_view, bg=BG)
        self.list_header.pack(fill="x", pady=(0, 8))
        self.file_count_lbl = tk.Label(
            self.list_header, text="0 files", bg=BG, fg=TEXT_SECONDARY, font=FONT_UI_SM
        )
        self.file_count_lbl.pack(side="left")
        clear_btn = tk.Label(
            self.list_header, text="Clear all", bg=BG, fg=TEXT_MUTED, font=FONT_UI_SM, cursor="hand2"
        )
        clear_btn.pack(side="right")
        clear_btn.bind("<Button-1>", lambda _e: self.clear_files())
        clear_btn.bind("<Enter>", lambda _e: clear_btn.configure(fg=ERROR))
        clear_btn.bind("<Leave>", lambda _e: clear_btn.configure(fg=TEXT_MUTED))
        add_btn = tk.Label(
            self.list_header, text="+ Add", bg=BG, fg=ACCENT_TEXT, font=FONT_UI_SM, cursor="hand2"
        )
        add_btn.pack(side="right", padx=(0, 14))
        add_btn.bind("<Button-1>", lambda _e: self.browse_files())

        # Scrollable file list
        list_wrap = tk.Frame(self.files_view, bg=BG)
        list_wrap.pack(fill="both", expand=True)

        self.list_canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0, bd=0)
        self.list_scroll = tk.Scrollbar(
            list_wrap, orient="vertical", command=self.list_canvas.yview,
            bg=BG, troughcolor=BG, activebackground=BORDER, width=10,
        )
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)
        self.list_scroll.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)

        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self._list_window = self.list_canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind(
            "<Configure>",
            lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")),
        )
        self.list_canvas.bind(
            "<Configure>",
            lambda e: self.list_canvas.itemconfigure(self._list_window, width=e.width),
        )
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Format section (convert mode)
        self.fmt_section = tk.Frame(self.files_view, bg=BG)
        self.fmt_section.pack(fill="x", pady=(14, 0))

        fmt_head = tk.Frame(self.fmt_section, bg=BG)
        fmt_head.pack(fill="x")
        tk.Label(
            fmt_head, text="Convert to", bg=BG, fg=TEXT_SECONDARY, font=FONT_UI_SM
        ).pack(side="left")
        self.auto_badge = tk.Label(
            fmt_head,
            text="AUTO",
            bg=SUCCESS_SOFT,
            fg=SUCCESS,
            font=("SF Pro Text", 10, "bold"),
            padx=6,
            pady=2,
        )
        self.auto_badge.pack(side="left", padx=(8, 0))

        self.chip_host = tk.Frame(self.fmt_section, bg=BG)
        self.chip_host.pack(fill="x", pady=(8, 0))

        # Compress section (compress mode — hidden by default)
        self.compress_section = tk.Frame(self.files_view, bg=BG)
        comp_head = tk.Frame(self.compress_section, bg=BG)
        comp_head.pack(fill="x")
        tk.Label(
            comp_head, text="Compression", bg=BG, fg=TEXT_SECONDARY, font=FONT_UI_SM
        ).pack(side="left")
        self.compress_target_lbl = tk.Label(
            comp_head,
            text="",
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
        )
        self.compress_target_lbl.pack(side="left", padx=(10, 0))

        self.preset_host = tk.Frame(self.compress_section, bg=BG)
        self.preset_host.pack(fill="x", pady=(8, 0))
        for key, cfg in COMPRESS_PRESETS.items():
            chip = Chip(
                self.preset_host,
                key=key,
                label=cfg["label"],
                command=self._on_preset_select,
            )
            chip.pack(side="left", padx=(0, 6))
            self._preset_chips[key] = chip
        self._preset_chips[self._compress_preset].set_active(True)

        self.compress_desc = tk.Label(
            self.compress_section,
            text=COMPRESS_PRESETS[self._compress_preset]["desc"],
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
            anchor="w",
        )
        self.compress_desc.pack(fill="x", pady=(8, 0))

        # Options row: quality (convert) + output dir
        opts = tk.Frame(self.files_view, bg=BG)
        opts.pack(fill="x", pady=(14, 0))
        self._opts_frame = opts

        self.q_frame = tk.Frame(opts, bg=BG)
        self.q_frame.pack(side="left")
        tk.Label(self.q_frame, text="Quality", bg=BG, fg=TEXT_MUTED, font=FONT_UI_XS).pack(
            side="left"
        )
        self.quality_var = tk.IntVar(value=100)
        self.quality_scale = tk.Scale(
            self.q_frame,
            from_=1,
            to=100,
            orient="horizontal",
            variable=self.quality_var,
            bg=BG,
            fg=TEXT_SECONDARY,
            highlightthickness=0,
            troughcolor=SURFACE_RAISED,
            activebackground=ACCENT,
            length=130,
            showvalue=0,
            bd=0,
            sliderrelief="flat",
            width=10,
        )
        self.quality_scale.pack(side="left", padx=(8, 6))
        self.quality_lbl = tk.Label(
            self.q_frame, text="100", bg=BG, fg=TEXT, font=FONT_UI_SM, width=4
        )
        self.quality_lbl.pack(side="left")
        self.quality_var.trace_add("write", lambda *_: self._on_quality_change())

        out_frame = tk.Frame(opts, bg=BG)
        out_frame.pack(side="right")
        self.out_dir_lbl = tk.Label(
            out_frame,
            text="Same folder as source",
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
            cursor="hand2",
        )
        self.out_dir_lbl.pack(side="left")
        self.out_dir_lbl.bind("<Button-1>", lambda _e: self.choose_output_dir())
        change = tk.Label(
            out_frame, text="Change", bg=BG, fg=ACCENT_TEXT, font=FONT_UI_XS, cursor="hand2"
        )
        change.pack(side="left", padx=(8, 0))
        change.bind("<Button-1>", lambda _e: self.choose_output_dir())

        # Batch size estimate strip
        self.estimate_strip = tk.Frame(
            self.files_view,
            bg=SURFACE,
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
        )
        est_inner = tk.Frame(self.estimate_strip, bg=SURFACE)
        est_inner.pack(fill="x", padx=14, pady=10)
        tk.Label(
            est_inner,
            text="Estimated result",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_UI_XS,
        ).pack(side="left")
        self.estimate_total_lbl = tk.Label(
            est_inner,
            text="—",
            bg=SURFACE,
            fg=TEXT,
            font=FONT_UI_BOLD,
            anchor="e",
        )
        self.estimate_total_lbl.pack(side="right")
        self.estimate_detail_lbl = tk.Label(
            est_inner,
            text="",
            bg=SURFACE,
            fg=TEXT_SECONDARY,
            font=FONT_UI_XS,
            anchor="e",
        )
        self.estimate_detail_lbl.pack(side="right", padx=(0, 12))

        # Footer actions
        footer = tk.Frame(self.shell, bg=BG)
        footer.pack(fill="x", pady=(16, 0))

        self.progress_track = tk.Frame(footer, bg=SURFACE_RAISED, height=3)
        self.progress_track.pack(fill="x", pady=(0, 12))
        self.progress_track.pack_propagate(False)
        self.progress_fill = tk.Frame(self.progress_track, bg=ACCENT, width=0, height=3)
        self.progress_fill.place(x=0, y=0, relheight=1, width=0)

        actions = tk.Frame(footer, bg=BG)
        actions.pack(fill="x")

        self.status_lbl = tk.Label(
            actions, text="Ready", bg=BG, fg=TEXT_MUTED, font=FONT_UI_SM, anchor="w"
        )
        self.status_lbl.pack(side="left")

        self.reveal_btn = tk.Label(
            actions,
            text="Show in Finder",
            bg=BG,
            fg=TEXT_MUTED,
            font=FONT_UI_SM,
            cursor="hand2",
            padx=10,
        )
        self.reveal_btn.pack(side="right", padx=(0, 10))
        self.reveal_btn.bind("<Button-1>", lambda _e: self.reveal_output())

        self.convert_btn = tk.Label(
            actions,
            text="Convert",
            bg=ACCENT,
            fg=CHIP_ACTIVE_TEXT,
            font=FONT_UI_BOLD,
            padx=22,
            pady=10,
            cursor="hand2",
        )
        self.convert_btn.pack(side="right")
        self.convert_btn.bind("<Button-1>", lambda _e: self.start_convert())
        self.convert_btn.bind(
            "<Enter>",
            lambda _e: self.convert_btn.configure(bg=ACCENT_TEXT)
            if not self._busy
            else None,
        )
        self.convert_btn.bind(
            "<Leave>",
            lambda _e: self.convert_btn.configure(bg=ACCENT if not self._busy else SURFACE_RAISED),
        )
        self.convert_btn.bind(
            "<ButtonPress-1>",
            lambda _e: self.convert_btn.configure(bg="#4a7ad4") if not self._busy else None,
        )

        # Log (collapsible slim)
        self.log_frame = tk.Frame(self.shell, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        self.log_text = tk.Text(
            self.log_frame,
            height=3,
            bg=SURFACE,
            fg=TEXT_MUTED,
            insertbackground=TEXT,
            relief="flat",
            font=FONT_MONO,
            wrap="word",
            state="disabled",
            padx=10,
            pady=8,
            highlightthickness=0,
            bd=0,
        )
        self.log_text.pack(fill="x")
        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("err", foreground=ERROR)
        self.log_text.tag_configure("info", foreground=TEXT_MUTED)

        # Shortcuts
        self.root.bind("<Command-o>", lambda _e: self.browse_files())
        self.root.bind("<Command-Return>", lambda _e: self.start_convert())
        self.root.bind("<Command-BackSpace>", lambda _e: self.clear_files())

    def _make_pill(self, parent, text: str) -> tk.Label:
        lbl = tk.Label(
            parent,
            text=text,
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=("SF Pro Text", 10),
            padx=8,
            pady=3,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        lbl.pack(side="left", padx=(6, 0))
        return lbl

    def _refresh_pills(self) -> None:
        def set_pill(lbl: tk.Label, ok: bool, ok_text: str, bad_text: str) -> None:
            if ok:
                lbl.configure(text=ok_text, fg=SUCCESS, highlightbackground=SUCCESS_SOFT, bg=SUCCESS_SOFT)
            else:
                lbl.configure(text=bad_text, fg=WARN, highlightbackground=BORDER, bg=SURFACE)

        set_pill(self.pill_ffmpeg, has_ffmpeg(), "ffmpeg", "no ffmpeg")
        set_pill(self.pill_heic, HEIF_SUPPORTED, "HEIC", "no HEIC")
        set_pill(self.pill_drop, HAS_DND, "drop", "no drop")

    def _on_mousewheel(self, event) -> None:
        if self.files:
            self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── State: empty vs files ──────────────────────────────────────────────
    def _show_empty_state(self) -> None:
        self.files_view.pack_forget()
        self.log_frame.pack_forget()
        self.empty_state.pack(fill="both", expand=True)
        self.convert_btn.configure(bg=SURFACE_RAISED, fg=TEXT_MUTED)
        self.status_lbl.configure(text="Drop files to begin")
        self.subtitle.configure(text="Drop files · convert or compress")

    def _show_files_state(self) -> None:
        self.empty_state.pack_forget()
        self.files_view.pack(fill="both", expand=True)
        self.log_frame.pack(fill="x", pady=(12, 0))
        self.convert_btn.configure(bg=ACCENT, fg=CHIP_ACTIVE_TEXT)
        self._apply_mode_ui()

    def _on_quality_change(self) -> None:
        q = int(self.quality_var.get())
        if q >= 100:
            self.quality_lbl.configure(text="Max")
        else:
            self.quality_lbl.configure(text=str(q))
        self._schedule_estimates()

    def _set_mode(self, mode: str) -> None:
        if mode not in ("convert", "compress"):
            return
        self._mode = mode
        self._apply_mode_ui()
        self._schedule_estimates()

    def _apply_mode_ui(self) -> None:
        opts = self._opts_frame
        active = bool(self.files) and not self._busy

        if self._mode == "convert":
            self.mode_convert_btn.configure(
                bg=ACCENT, fg=CHIP_ACTIVE_TEXT, font=FONT_UI_BOLD,
                highlightthickness=0,
            )
            self.mode_compress_btn.configure(
                bg=SURFACE, fg=TEXT_SECONDARY, font=FONT_UI_SM,
                highlightthickness=1, highlightbackground=BORDER,
            )
            self.mode_hint.configure(text="Change format  ·  quality defaults to Max")
            if self.files:
                self.suggest_banner.pack(fill="x", pady=(0, 12), before=self.list_header)
                self.compress_section.pack_forget()
                self.fmt_section.pack(fill="x", pady=(14, 0), before=opts)
                self.q_frame.pack(side="left")
                self.estimate_strip.pack(fill="x", pady=(10, 0), before=opts)
            self.convert_btn.configure(
                text="Convert",
                bg=ACCENT if active else SURFACE_RAISED,
                fg=CHIP_ACTIVE_TEXT if active else TEXT_MUTED,
            )
        else:
            self.mode_compress_btn.configure(
                bg=ACCENT, fg=CHIP_ACTIVE_TEXT, font=FONT_UI_BOLD,
                highlightthickness=0,
            )
            self.mode_convert_btn.configure(
                bg=SURFACE, fg=TEXT_SECONDARY, font=FONT_UI_SM,
                highlightthickness=1, highlightbackground=BORDER,
            )
            self.mode_hint.configure(text="Shrink files  ·  keep same kind of media")
            if self.files:
                self.suggest_banner.pack_forget()
                self.fmt_section.pack_forget()
                self.q_frame.pack_forget()
                self.compress_section.pack(fill="x", pady=(14, 0), before=opts)
                self.estimate_strip.pack(fill="x", pady=(10, 0), before=opts)
                self._refresh_compress_targets()
            self.convert_btn.configure(
                text="Compress",
                bg=ACCENT if active else SURFACE_RAISED,
                fg=CHIP_ACTIVE_TEXT if active else TEXT_MUTED,
            )

    def _on_preset_select(self, key: str) -> None:
        self._compress_preset = key
        for k, chip in self._preset_chips.items():
            chip.set_active(k == key)
        self.compress_desc.configure(text=COMPRESS_PRESETS[key]["desc"])
        self._refresh_compress_targets()
        self._schedule_estimates()

    def _refresh_compress_targets(self) -> None:
        if not self.files:
            self.compress_target_lbl.configure(text="")
            return
        from converter.engine import compress_target_format

        prefer = COMPRESS_PRESETS[self._compress_preset]["prefer_efficient"]
        targets = [compress_target_format(f, prefer_efficient=prefer) for f in self.files]
        # Summarize
        from collections import Counter

        counts = Counter(targets)
        parts = [
            f"{FORMAT_LABELS.get(fmt, fmt.upper())}×{n}" if n > 1 else FORMAT_LABELS.get(fmt, fmt.upper())
            for fmt, n in counts.most_common()
        ]
        self.compress_target_lbl.configure(text="→ " + " · ".join(parts))

    # ── Size estimates ────────────────────────────────────────────────────
    def _schedule_estimates(self, delay_ms: int = 80) -> None:
        """Debounced recompute so slider drags stay smooth."""
        if not self.files or self._busy:
            return
        self._estimate_job += 1
        job = self._estimate_job
        self.root.after(delay_ms, lambda: self._run_estimates(job))

    def _run_estimates(self, job: int) -> None:
        if job != self._estimate_job or not self.files or self._busy:
            return

        mode = self._mode
        fmt = self._selected_format
        quality = int(self.quality_var.get())
        preset = self._compress_preset
        files = list(self.files)

        def worker() -> None:
            per, total = estimate_batch(
                files,
                mode=mode,
                fmt=fmt,
                quality=quality,
                preset=preset,
            )
            if job != self._estimate_job:
                return
            self.root.after(0, lambda: self._apply_estimates(job, files, per, total))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_estimates(
        self,
        job: int,
        files: list[Path],
        per: list[SizeEstimate | None],
        total: SizeEstimate | None,
    ) -> None:
        if job != self._estimate_job or self._busy:
            return
        for path, est in zip(files, per):
            row = self.file_rows.get(str(path))
            if row:
                row.set_estimate(est)
        self._paint_total_estimate(total)

    def _paint_total_estimate(self, total: SizeEstimate | None) -> None:
        if not total or total.source_bytes <= 0:
            self.estimate_total_lbl.configure(text="—", fg=TEXT)
            self.estimate_detail_lbl.configure(text="")
            return

        src = format_bytes(total.source_bytes)
        out = format_bytes(total.estimated_bytes)
        pct = total.pct_change

        if abs(pct) < 3:
            detail = f"{src} → {out}"
            fg = TEXT_SECONDARY
            headline = "similar size"
        elif pct < 0:
            saved = format_bytes(total.source_bytes - total.estimated_bytes)
            detail = f"{src} → {out}"
            fg = SUCCESS
            headline = f"−{abs(pct):.0f}%  ·  save {saved}"
        else:
            detail = f"{src} → {out}"
            fg = WARN if pct < 40 else ERROR
            headline = f"+{pct:.0f}% larger"

        self.estimate_detail_lbl.configure(text=detail, fg=TEXT_SECONDARY)
        self.estimate_total_lbl.configure(text=headline, fg=fg)

    # ── DnD ────────────────────────────────────────────────────────────────
    def _wire_dnd(self) -> None:
        if not HAS_DND:
            return
        targets = [
            self.root,
            self.empty_state,
            self.drop_inner,
            self.content,
            self.list_canvas,
            self.list_inner,
        ]
        for widget in targets:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            except tk.TclError:
                continue

    def _on_drag_enter(self, event):
        self.empty_state.configure(highlightbackground=ACCENT, highlightthickness=2)
        return event.action

    def _on_drag_leave(self, event):
        self.empty_state.configure(highlightbackground=BORDER, highlightthickness=1)
        return event.action

    def _on_drop(self, event):
        self.empty_state.configure(highlightbackground=BORDER, highlightthickness=1)
        self.add_files(parse_dropped_paths(event.data))
        return event.action

    # ── File management ────────────────────────────────────────────────────
    def add_files(self, paths: list[Path]) -> None:
        added = 0
        for p in paths:
            p = p.expanduser().resolve()
            if not p.is_file() or p in self.files:
                continue
            if detect_kind(p) is None:
                self.log(f"Skipped unsupported: {p.name}", "err")
                continue
            self.files.append(p)
            row = FileRow(self.list_inner, p, on_remove=self.remove_file)
            row.pack(fill="x", pady=3)
            self.file_rows[str(p)] = row
            added += 1

        if added:
            # New files → re-enable smart guess unless user locked a format mid-batch
            if not self._format_override:
                self._selected_format = None
            self._show_files_state()
            self._refresh_file_count()
            self._refresh_formats_and_guess()
            self._schedule_estimates()

    def remove_file(self, path: Path) -> None:
        key = str(path)
        if key in self.file_rows:
            self.file_rows[key].destroy()
            del self.file_rows[key]
        self.files = [f for f in self.files if f != path]
        if not self.files:
            self._format_override = False
            self._selected_format = None
            self._show_empty_state()
        else:
            self._refresh_file_count()
            self._refresh_formats_and_guess()
            self._schedule_estimates()

    def clear_files(self) -> None:
        for row in list(self.file_rows.values()):
            row.destroy()
        self.file_rows.clear()
        self.files.clear()
        self._format_override = False
        self._selected_format = None
        self._last_outputs = []
        self._estimate_job += 1
        self._set_progress(0)
        self._show_empty_state()

    def _refresh_file_count(self) -> None:
        n = len(self.files)
        kinds = {detect_kind(f) for f in self.files}
        kind_txt = " · ".join(sorted(k for k in kinds if k))
        self.file_count_lbl.configure(
            text=f"{n} file{'s' if n != 1 else ''}  ·  {kind_txt}" if kind_txt else f"{n} files"
        )
        self.status_lbl.configure(text=f"{n} ready")

    def browse_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select files to convert",
            filetypes=[
                (
                    "Media",
                    "*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif *.gif *.heic *.heif "
                    "*.avif *.ico *.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv "
                    "*.mp3 *.wav *.aac *.m4a *.flac *.ogg *.opus *.aiff *.aif",
                ),
                ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif *.gif *.heic *.heif *.avif *.ico"),
                ("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.flv"),
                ("Audio", "*.mp3 *.wav *.aac *.m4a *.flac *.ogg *.opus *.aiff *.aif *.wma"),
                ("All", "*.*"),
            ],
        )
        if paths:
            self.add_files([Path(p) for p in paths])

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="Save converted files to…")
        if path:
            self.output_dir = Path(path)
            # Truncate long paths for display
            display = str(self.output_dir)
            if len(display) > 42:
                display = "…" + display[-40:]
            self.out_dir_lbl.configure(text=display, fg=TEXT_SECONDARY)

    # ── Format chips + smart guess ─────────────────────────────────────────
    def _refresh_formats_and_guess(self) -> None:
        allowed = common_formats(self.files)
        # Rebuild chips
        for child in self.chip_host.winfo_children():
            child.destroy()
        self._chips.clear()

        if not allowed:
            tk.Label(
                self.chip_host,
                text="No shared format for this mix of files",
                bg=BG,
                fg=ERROR,
                font=FONT_UI_SM,
            ).pack(anchor="w")
            self.suggest_title.configure(text="Can't convert together")
            self.suggest_reason.configure(text="Try selecting only images, only video, or only audio")
            return

        # Group chips: preferred first from guesser, then rest
        best, reason, alts = self.guesser.guess(self.files)
        ranked = [g.format for g in alts if g.format in allowed]
        for f in allowed:
            if f not in ranked:
                ranked.append(f)

        # Wrap chips into rows of ~8
        row = tk.Frame(self.chip_host, bg=BG)
        row.pack(fill="x")
        count = 0
        for key in ranked:
            if count and count % 9 == 0:
                row = tk.Frame(self.chip_host, bg=BG)
                row.pack(fill="x", pady=(6, 0))
            chip = Chip(
                row,
                key=key,
                label=FORMAT_LABELS.get(key, key.upper()),
                command=self._on_chip_select,
            )
            chip.pack(side="left", padx=(0, 6), pady=2)
            self._chips[key] = chip
            count += 1

        # Apply selection
        if self._format_override and self._selected_format in allowed:
            chosen = self._selected_format
            self._set_suggestion_ui(best, reason, overridden=True)
        else:
            chosen = best or allowed[0]
            self._selected_format = chosen
            self._format_override = False
            self._set_suggestion_ui(best, reason, overridden=False)

        self._paint_chips(chosen)
        self._schedule_estimates()

    def _set_suggestion_ui(self, best: str | None, reason: str, *, overridden: bool) -> None:
        if best:
            label = FORMAT_LABELS.get(best, best.upper())
            self.suggest_title.configure(text=label)
            self.suggest_reason.configure(text=reason)
            if overridden:
                self.auto_badge.configure(text="MANUAL", bg=SURFACE_RAISED, fg=TEXT_SECONDARY)
                self.use_suggest_btn.configure(text=f"Use {label}")
            else:
                self.auto_badge.configure(text="AUTO", bg=SUCCESS_SOFT, fg=SUCCESS)
                self.use_suggest_btn.configure(text="Applied")
        else:
            self.suggest_title.configure(text="—")
            self.suggest_reason.configure(text=reason)

    def _paint_chips(self, active: str | None) -> None:
        for key, chip in self._chips.items():
            chip.set_active(key == active)

    def _on_chip_select(self, key: str) -> None:
        self._selected_format = key
        self._format_override = True
        self._paint_chips(key)
        best, reason, _ = self.guesser.guess(self.files)
        self._set_suggestion_ui(best, reason, overridden=True)
        self.status_lbl.configure(
            text=f"→ {FORMAT_LABELS.get(key, key.upper())}"
        )
        self._schedule_estimates()

    def _apply_suggestion(self) -> None:
        best, reason, _ = self.guesser.guess(self.files)
        if not best:
            return
        self._selected_format = best
        self._format_override = False
        self._paint_chips(best)
        self._set_suggestion_ui(best, reason, overridden=False)
        self.status_lbl.configure(text=f"Using {FORMAT_LABELS.get(best, best.upper())}")
        self._schedule_estimates()

    # ── Convert / Compress ─────────────────────────────────────────────────
    def start_convert(self) -> None:
        if self._busy or not self.files:
            return

        if self._mode == "compress":
            self._start_compress()
            return

        fmt = self._selected_format
        if not fmt:
            messagebox.showinfo("Pick a format", "Choose an output format first.")
            return

        needs_ff = any(detect_kind(f) in ("video", "audio") for f in self.files)
        if needs_ff and not has_ffmpeg():
            messagebox.showerror(
                "ffmpeg required",
                "Video/audio conversion needs ffmpeg.\n\nbrew install ffmpeg",
            )
            return

        self._busy = True
        self.convert_btn.configure(bg=SURFACE_RAISED, fg=TEXT_MUTED, text="Converting…")
        self._set_progress(0)
        self._clear_log()
        self.status_lbl.configure(text="Converting…")

        files = list(self.files)
        out_dir = self.output_dir
        quality = int(self.quality_var.get())

        def worker() -> None:
            results = []
            total = len(files)
            for i, src in enumerate(files):
                self.root.after(0, lambda s=src: self._row_status(s, "running"))
                r = convert_file(
                    src,
                    fmt,
                    output_dir=out_dir,
                    quality=quality,
                    log=lambda msg: self.log(msg, "info"),
                )
                results.append(r)
                if r.success:
                    self.root.after(0, lambda s=src: self._row_status(s, "ok"))
                    self.log(
                        f"✓ {r.source.name} → {r.output.name if r.output else '?'}",
                        "ok",
                    )
                else:
                    self.root.after(
                        0, lambda s=src, m=r.message: self._row_status(s, "err", m[:24])
                    )
                    self.log(f"✗ {r.source.name}: {r.message}", "err")
                self.root.after(0, lambda i=i, t=total: self._set_progress((i + 1) / t))
            self.root.after(0, lambda: self._on_done(results, fmt, mode="convert"))

        threading.Thread(target=worker, daemon=True).start()

    def _start_compress(self) -> None:
        needs_ff = any(detect_kind(f) in ("video", "audio") for f in self.files)
        if needs_ff and not has_ffmpeg():
            messagebox.showerror(
                "ffmpeg required",
                "Video/audio compression needs ffmpeg.\n\nbrew install ffmpeg",
            )
            return

        self._busy = True
        self.convert_btn.configure(bg=SURFACE_RAISED, fg=TEXT_MUTED, text="Compressing…")
        self._set_progress(0)
        self._clear_log()
        self.status_lbl.configure(text="Compressing…")

        files = list(self.files)
        out_dir = self.output_dir
        preset = self._compress_preset

        def worker() -> None:
            results = []
            total = len(files)
            for i, src in enumerate(files):
                self.root.after(0, lambda s=src: self._row_status(s, "running"))
                r = compress_file(
                    src,
                    preset=preset,
                    output_dir=out_dir,
                    log=lambda msg: self.log(msg, "info"),
                )
                results.append(r)
                if r.success and r.output:
                    try:
                        src_sz = src.stat().st_size
                        out_sz = r.output.stat().st_size
                        ratio = f"  {self._fmt_bytes(src_sz)} → {self._fmt_bytes(out_sz)}"
                    except OSError:
                        ratio = ""
                    self.root.after(0, lambda s=src: self._row_status(s, "ok"))
                    self.log(f"✓ {r.source.name} → {r.output.name}{ratio}  {r.message}", "ok")
                else:
                    self.root.after(
                        0, lambda s=src, m=r.message: self._row_status(s, "err", m[:24])
                    )
                    self.log(f"✗ {r.source.name}: {r.message}", "err")
                self.root.after(0, lambda i=i, t=total: self._set_progress((i + 1) / t))
            self.root.after(0, lambda: self._on_done(results, None, mode="compress"))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    def _row_status(self, path: Path, status: str, detail: str = "") -> None:
        row = self.file_rows.get(str(path))
        if row:
            row.set_status(status, detail)

    def _on_done(self, results, fmt: str | None, mode: str = "convert") -> None:
        self._busy = False
        btn_label = "Compress" if mode == "compress" else "Convert"
        self.convert_btn.configure(bg=ACCENT, fg=CHIP_ACTIVE_TEXT, text=btn_label)
        ok = [r for r in results if r.success]
        fail = len(results) - len(ok)
        self._last_outputs = [r.output for r in ok if r.output]

        # Learn from successful format conversions only
        if mode == "convert" and fmt:
            for r in ok:
                self.guesser.record(r.source, fmt)

        # Size summary for compress
        extra = ""
        if mode == "compress" and ok:
            try:
                src_total = sum(r.source.stat().st_size for r in ok)
                out_total = sum(r.output.stat().st_size for r in ok if r.output)
                if src_total > 0:
                    saved = (1 - out_total / src_total) * 100
                    extra = f"  ·  {saved:.0f}% smaller overall"
            except OSError:
                pass

        self.status_lbl.configure(
            text=f"Done — {len(ok)} ok" + (f", {fail} failed" if fail else "") + extra
        )
        if self._last_outputs:
            self.reveal_btn.configure(fg=ACCENT_TEXT)
        if fail and not ok:
            messagebox.showerror("Failed", f"All {fail} file(s) failed. See log.")
        elif fail:
            messagebox.showwarning("Partial", f"{len(ok)} ok, {fail} failed.")

    def _set_progress(self, fraction: float) -> None:
        self.progress_track.update_idletasks()
        w = self.progress_track.winfo_width()
        self.progress_fill.place(x=0, y=0, relheight=1, width=max(0, int(w * fraction)))

    # ── Log / reveal ───────────────────────────────────────────────────────
    def log(self, msg: str, tag: str = "info") -> None:
        def _write() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n", tag)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, _write)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def reveal_output(self) -> None:
        if not self._last_outputs:
            return
        first = self._last_outputs[0]
        if sys.platform == "darwin":
            if first.is_file():
                subprocess.run(["open", "-R", str(first)], check=False)
            else:
                subprocess.run(["open", str(first.parent)], check=False)
        else:
            subprocess.run(["xdg-open", str(first.parent)], check=False)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ConverterApp().run()


if __name__ == "__main__":
    main()
