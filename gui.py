"""
Brightspace Page Automator
GUI architecture reused from brightspace-quiz-automator/gui.py:
  same color palette, same worker-thread + queue + after() pattern.
"""
import asyncio
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk

try:
    from CTkMessagebox import CTkMessagebox
except ImportError:
    CTkMessagebox = None

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Palette (identical to quiz automator) ────────────────────────────────────
_BG         = "#0f0f14"
_CARD       = "#17171f"
_ACCENT     = "#0d9488"
_ACCENT_H   = "#14b8a6"
_DIVIDER    = "#222230"
_TEXT_DIM   = "#9aa0b8"
_TEXT_FAINT = "#5d6378"
_LOG_BG     = "#0b0b10"
_LOG_BORDER = "#1c1c26"

ctk.ThemeManager.theme["CTk"]["fg_color"]           = ["#f3f3f6", _BG]
ctk.ThemeManager.theme["CTkToplevel"]["fg_color"]    = ["#f3f3f6", _BG]
ctk.ThemeManager.theme["CTkFrame"]["fg_color"]       = ["#ebebec", _CARD]
ctk.ThemeManager.theme["CTkButton"]["fg_color"]      = [_ACCENT, _ACCENT]
ctk.ThemeManager.theme["CTkButton"]["hover_color"]   = [_ACCENT_H, _ACCENT_H]
ctk.ThemeManager.theme["CTkButton"]["corner_radius"] = 8
ctk.ThemeManager.theme["CTkEntry"]["fg_color"]       = ["#f9f9fa", "#101016"]
ctk.ThemeManager.theme["CTkEntry"]["border_color"]   = ["#979da2", "#2a2a38"]
ctk.ThemeManager.theme["CTkTextbox"]["fg_color"]     = ["#f9f9fa", "#101016"]

# ── Log color tags ────────────────────────────────────────────────────────────
_TAG_COLORS = {
    "success": "#4caf50",   # green
    "error":   "#ef5350",   # red
    "warning": "#f0a500",   # amber
    "step":    "#4dd0e1",   # cyan
    "info":    "#b8c4da",   # default
    "dim":     "#3a3f52",   # muted separator lines
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Brightspace Page Automator")
        self.geometry("780x560")
        self.minsize(600, 420)
        self.configure(fg_color=_BG)

        self._log_queue = queue.Queue()
        self._build_ui()
        self.after(100, self._poll_log)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(24, 0))
        ctk.CTkLabel(
            hdr,
            text="Brightspace Page Automator",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Paste a page URL — the automator will click the  ⋯  property icon",
            font=ctk.CTkFont(size=12),
            text_color=_TEXT_DIM,
        ).pack(anchor="w", pady=(4, 0))
        ctk.CTkFrame(self, height=1, fg_color=_DIVIDER).pack(fill="x", padx=28, pady=(14, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(16, 16))

        # URL label
        ctk.CTkLabel(
            body,
            text="BRIGHTSPACE PAGE URL",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))

        # URL input row
        url_row = ctk.CTkFrame(body, fg_color="transparent")
        url_row.pack(fill="x", pady=(0, 16))
        url_row.columnconfigure(0, weight=1)

        self._url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="https://learn.okanagancollege.ca/d2l/home/…",
            height=42,
            font=ctk.CTkFont(size=13),
        )
        self._url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._bind_paste_menu(self._url_entry)

        self._run_btn = ctk.CTkButton(
            url_row,
            text="▶  Start",
            width=110,
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_run,
        )
        self._run_btn.grid(row=0, column=1)

        # Log label
        ctk.CTkLabel(
            body,
            text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))

        # Log box
        border = ctk.CTkFrame(body, fg_color=_LOG_BORDER, corner_radius=8)
        border.pack(fill="both", expand=True)
        self._log_box = ctk.CTkTextbox(
            border,
            state="disabled",
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=_LOG_BG,
            corner_radius=6,
            text_color=_TAG_COLORS["info"],
            border_width=0,
        )
        self._log_box.pack(fill="both", expand=True, padx=2, pady=2)

        # Wire up color tags on the underlying tk.Text widget
        for tag, color in _TAG_COLORS.items():
            self._log_box._textbox.tag_configure(tag, foreground=color)

    def _bind_paste_menu(self, entry: ctk.CTkEntry):
        import tkinter as tk

        def paste():
            try:
                entry._entry.insert("insert", entry.clipboard_get())
            except Exception:
                pass

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Paste",      command=paste)
        menu.add_command(label="Select All", command=lambda: entry._entry.selection_range(0, "end"))
        menu.add_command(label="Clear",      command=lambda: entry.delete(0, "end"))

        entry.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ── Run ───────────────────────────────────────────────────────────────────

    def _start_run(self):
        url = self._url_entry.get().strip()
        if not url:
            self._log_append("⚠  Paste a Brightspace URL first.", "warning")
            return

        self._run_btn.configure(state="disabled", text="Running…")
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

        q = self._log_queue

        def worker():
            class _Capture:
                def write(self, t):
                    if t.strip():
                        q.put((t.rstrip(), "dim"))
                def flush(self): pass

            old, sys.stdout = sys.stdout, _Capture()
            done_sent = [False]

            def on_complete():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))

            try:
                from automator import run as automator_run
                asyncio.run(automator_run(
                    url=url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_complete,
                ))
            except Exception as e:
                q.put((f"✗  {e}", "error"))
            finally:
                sys.stdout = old
                on_complete()  # no-op if already called

        threading.Thread(target=worker, daemon=True).start()

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log_append(self, text: str, tag: str = "info"):
        tb = self._log_box._textbox
        tb.configure(state="normal")
        tb.insert("end", text + "\n", tag)
        tb.see("end")
        tb.configure(state="disabled")

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.configure(state="normal", text="▶  Start")
                else:
                    self._log_append(msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    App().mainloop()
