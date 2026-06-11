"""
Brightspace Page Automator
GUI architecture reused from brightspace-quiz-automator/gui.py:
  same color palette, same worker-thread + queue + after() pattern.
"""
import asyncio
import json
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

# ── Config persistence ────────────────────────────────────────────────────────
_CONFIG_PATH = Path.home() / ".local" / "share" / "BrightspaceAutomator" / "config.json"

# ── Page themes ───────────────────────────────────────────────────────────────
PAGE_THEMES = {
    "blue":   dict(primary="#0E72ED", mid="#2D8CFF", accent="#00C6D7",
                   bg_from="#eaf4ff", bg_to="#dffcff", shadow_rgb="14,114,237",   circle="#2D8CFF"),
    "teal":   dict(primary="#0D9488", mid="#14B8A6", accent="#2DD4BF",
                   bg_from="#eafaf8", bg_to="#f0fffc", shadow_rgb="13,148,136",   circle="#14B8A6"),
    "green":  dict(primary="#16A34A", mid="#22C55E", accent="#4ADE80",
                   bg_from="#eafff0", bg_to="#f0fff4", shadow_rgb="22,163,74",    circle="#22C55E"),
    "lime":   dict(primary="#65A30D", mid="#84CC16", accent="#BEF264",
                   bg_from="#f4ffea", bg_to="#f9ffe0", shadow_rgb="101,163,13",   circle="#84CC16"),
    "amber":  dict(primary="#D97706", mid="#F59E0B", accent="#FCD34D",
                   bg_from="#fffbea", bg_to="#fffef0", shadow_rgb="217,119,6",    circle="#F59E0B"),
    "orange": dict(primary="#EA580C", mid="#F97316", accent="#FDBA74",
                   bg_from="#fff3ea", bg_to="#fff9ef", shadow_rgb="234,88,12",    circle="#F97316"),
    "red":    dict(primary="#B91C1C", mid="#EF4444", accent="#FCA5A5",
                   bg_from="#ffeaea", bg_to="#fff0f0", shadow_rgb="185,28,28",    circle="#EF4444"),
    "pink":   dict(primary="#BE185D", mid="#EC4899", accent="#F9A8D4",
                   bg_from="#ffeaf5", bg_to="#fff0f8", shadow_rgb="190,24,93",    circle="#EC4899"),
    "purple": dict(primary="#6D28D9", mid="#8B5CF6", accent="#C4B5FD",
                   bg_from="#f3eaff", bg_to="#f8f0ff", shadow_rgb="109,40,217",   circle="#8B5CF6"),
}

# ── Palette ───────────────────────────────────────────────────────────────────
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

_TAG_COLORS = {
    "success": "#4caf50",
    "error":   "#ef5350",
    "warning": "#f0a500",
    "step":    "#4dd0e1",
    "info":    "#b8c4da",
    "dim":     "#3a3f52",
}


def _make_log_box(parent) -> ctk.CTkTextbox:
    border = ctk.CTkFrame(parent, fg_color=_LOG_BORDER, corner_radius=8)
    border.pack(fill="both", expand=True)
    box = ctk.CTkTextbox(
        border,
        state="disabled",
        font=ctk.CTkFont(family="Consolas", size=12),
        fg_color=_LOG_BG,
        corner_radius=6,
        text_color=_TAG_COLORS["info"],
        border_width=0,
    )
    box.pack(fill="both", expand=True, padx=2, pady=2)
    for tag, color in _TAG_COLORS.items():
        box._textbox.tag_configure(tag, foreground=color)
    return box


def _log_append(box: ctk.CTkTextbox, text: str, tag: str = "info") -> None:
    tb = box._textbox
    tb.configure(state="normal")
    tb.insert("end", text + "\n", tag)
    tb.see("end")
    tb.configure(state="disabled")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Page Changer")
        self.geometry("800x720")
        self.minsize(640, 580)
        self.configure(fg_color=_BG)
        self._set_window_icon()

        self._log_queue      = queue.Queue()
        self._sm_log_queue   = queue.Queue()
        self._col_log_queue  = queue.Queue()
        self._response_queue = queue.Queue()   # GUI → worker: page selection
        self._selected_theme     = "blue"
        self._swatch_frames      = {}
        self._selected_col_theme = "blue"
        self._col_swatch_frames  = {}
        self._build_ui()
        self.after(100, self._poll_log)
        self.after(100, self._sm_poll_log)
        self.after(100, self._col_poll_log)

    # ── Top-level UI ──────────────────────────────────────────────────────────

    def _set_window_icon(self):
        try:
            from PIL import Image, ImageDraw
            import io, tkinter as tk
            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([0, 0, size - 1, size - 1], fill="#0d9488")
            # lightning bolt polygon
            bolt = [(38, 6), (22, 34), (32, 34), (26, 58), (44, 28), (34, 28)]
            draw.polygon(bolt, fill="#ffffff")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            photo = tk.PhotoImage(data=buf.getvalue())
            self.iconphoto(True, photo)
            self._icon_photo = photo  # prevent GC
        except Exception:
            pass

    def _build_ui(self):
        # ── App header bar ────────────────────────────────────────────────────
        hbar = ctk.CTkFrame(self, fg_color=_CARD, height=52, corner_radius=0)
        hbar.pack(fill="x")
        hbar.pack_propagate(False)

        ctk.CTkLabel(
            hbar, text="⚡", font=ctk.CTkFont(size=24), text_color=_ACCENT,
        ).pack(side="left", padx=(14, 4), pady=10)

        ctk.CTkLabel(
            hbar, text="Page Changer",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(side="left", pady=10)

        ctk.CTkLabel(
            hbar, text="v0.5.0",
            font=ctk.CTkFont(size=11), text_color=_TEXT_FAINT,
        ).pack(side="right", padx=18, pady=10)

        ctk.CTkFrame(self, height=1, fg_color=_DIVIDER).pack(fill="x")

        tabview = ctk.CTkTabview(
            self,
            fg_color=_CARD,
            segmented_button_fg_color=_DIVIDER,
            segmented_button_selected_color=_ACCENT,
            segmented_button_selected_hover_color=_ACCENT_H,
            segmented_button_unselected_color=_DIVIDER,
            segmented_button_unselected_hover_color="#2a2a38",
            text_color=_TEXT_DIM,
        )
        tabview.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_automator_tab(tabview.add("⚡ Page Changer"))
        self._build_collector_tab(tabview.add("📦 Unit Collector"))
        self._build_style_migrator_tab(tabview.add("🎨 Style Migrator"))

    # ── Automator tab ─────────────────────────────────────────────────────────

    def _build_automator_tab(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 0))

        ctk.CTkLabel(
            hdr, text="⚡ Page Changer",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Paste a page URL — the automator will click the  ⋯  property icon",
            font=ctk.CTkFont(size=12), text_color=_TEXT_DIM,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            hdr, text="PAGE THEME",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(14, 6))

        swatches_row = ctk.CTkFrame(hdr, fg_color="transparent")
        swatches_row.pack(anchor="w")

        for name, theme in PAGE_THEMES.items():
            ring = ctk.CTkFrame(
                swatches_row, width=34, height=34, corner_radius=17,
                fg_color="transparent", border_width=2,
                border_color="#ffffff" if name == self._selected_theme else _BG,
            )
            ring.pack(side="left", padx=4)
            ring.pack_propagate(False)
            ctk.CTkButton(
                ring, width=26, height=26, corner_radius=13,
                fg_color=theme["circle"], hover_color=theme["mid"], text="",
                command=lambda n=name: self._select_theme(n),
            ).place(relx=0.5, rely=0.5, anchor="center")
            self._swatch_frames[name] = ring

        ctk.CTkFrame(parent, height=1, fg_color=_DIVIDER).pack(fill="x", padx=18, pady=(14, 0))

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(14, 14))

        ctk.CTkLabel(
            body, text="BRIGHTSPACE PAGE URL",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))

        url_row = ctk.CTkFrame(body, fg_color="transparent")
        url_row.pack(fill="x", pady=(0, 16))
        url_row.columnconfigure(0, weight=1)

        self._url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="https://learn.okanagancollege.ca/d2l/home/…",
            height=42, font=ctk.CTkFont(size=13),
        )
        self._url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._bind_paste_menu(self._url_entry)

        self._run_btn = ctk.CTkButton(
            url_row, text="▶  Start", width=110, height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_run,
        )
        self._run_btn.grid(row=0, column=1)

        ctk.CTkLabel(
            body, text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))

        self._log_box = _make_log_box(body)

    def _select_theme(self, name: str):
        if name == self._selected_theme:
            return
        self._swatch_frames[self._selected_theme].configure(border_color=_BG)
        self._selected_theme = name
        self._swatch_frames[name].configure(border_color="#ffffff")

    def _start_run(self):
        url = self._url_entry.get().strip()
        if not url:
            _log_append(self._log_box, "⚠  Paste a Brightspace URL first.", "warning")
            return

        try:
            from api_config import GEMINI_API_KEY
            gemini_api_key = GEMINI_API_KEY
        except ImportError:
            gemini_api_key = ""

        style_ref_path = Path(__file__).parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""


        self._run_btn.configure(state="disabled", text="Running…")
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

        q  = self._log_queue
        rq = self._response_queue

        def on_pages_found(pages):
            """Called from worker thread — puts pages in queue, blocks for user response."""
            q.put(("__PAGES__", pages))
            return rq.get(timeout=300)  # (start_idx, count)

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
                # Always reimport so live edits to automator.py take effect immediately
                import sys as _sys
                _sys.modules.pop('automator', None)
                from automator import run as automator_run
                asyncio.run(automator_run(
                    url=url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_complete,
                    gemini_api_key=gemini_api_key,
                    style_reference_html=style_reference_html,
                    theme_name=self._selected_theme,
                    on_pages_found=on_pages_found,
                ))
            except Exception as e:
                q.put((f"✗  {e}", "error"))
            finally:
                sys.stdout = old
                on_complete()

        threading.Thread(target=worker, daemon=True).start()

    def _show_pages_dialog(self, pages: list):
        import tkinter as tk
        dialog = ctk.CTkToplevel(self)
        dialog.title("Pages Found")
        dialog.geometry("480x460")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.configure(fg_color=_BG)

        ctk.CTkLabel(
            dialog, text=f"Found {len(pages)} pages in this section",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 8), padx=24, anchor="w")

        # Scrollable page list
        scroll = ctk.CTkScrollableFrame(dialog, fg_color=_CARD, height=180, corner_radius=10)
        scroll.pack(fill="x", padx=24, pady=(0, 16))
        for i, p in enumerate(pages, 1):
            ctk.CTkLabel(
                scroll, text=f"{i}.  {p['label']}",
                font=ctk.CTkFont(size=12), anchor="w", text_color=_TEXT_DIM,
            ).pack(fill="x", pady=2, padx=8)

        # Start from + how many
        fields = ctk.CTkFrame(dialog, fg_color="transparent")
        fields.pack(fill="x", padx=24, pady=(0, 20))
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)

        ctk.CTkLabel(fields, text="Start from page:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        start_entry = ctk.CTkEntry(fields, width=60, height=36, font=ctk.CTkFont(size=13))
        start_entry.insert(0, "1")
        start_entry.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(fields, text="  How many:", font=ctk.CTkFont(size=12)).grid(row=0, column=2, sticky="w", padx=(16, 8))
        count_entry = ctk.CTkEntry(fields, width=60, height=36, font=ctk.CTkFont(size=13))
        count_entry.insert(0, str(len(pages)))
        count_entry.grid(row=0, column=3, sticky="w")

        def on_run():
            try:
                start = max(1, int(start_entry.get())) - 1  # convert to 0-indexed
                count = max(1, int(count_entry.get()))
            except ValueError:
                start, count = 0, len(pages)
            self._response_queue.put((start, count))
            dialog.destroy()

        ctk.CTkButton(
            dialog, text="▶  Run",
            height=42, font=ctk.CTkFont(size=14, weight="bold"),
            command=on_run,
        ).pack(padx=24, fill="x")

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.configure(state="normal", text="▶  Start")
                elif msg == "__PAGES__":
                    self._show_pages_dialog(tag)
                    continue
                else:
                    _log_append(self._log_box, msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    # ── Unit Collector tab ────────────────────────────────────────────────────

    def _build_collector_tab(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 0))

        ctk.CTkLabel(
            hdr, text="Unit Collector",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Scrapes all topic pages from a unit and combines them into one collapsible HTML file",
            font=ctk.CTkFont(size=12), text_color=_TEXT_DIM,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            hdr, text="PAGE THEME",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(14, 6))

        swatches_row = ctk.CTkFrame(hdr, fg_color="transparent")
        swatches_row.pack(anchor="w")

        for name, theme in PAGE_THEMES.items():
            ring = ctk.CTkFrame(
                swatches_row, width=34, height=34, corner_radius=17,
                fg_color="transparent", border_width=2,
                border_color="#ffffff" if name == self._selected_col_theme else _BG,
            )
            ring.pack(side="left", padx=4)
            ring.pack_propagate(False)
            ctk.CTkButton(
                ring, width=26, height=26, corner_radius=13,
                fg_color=theme["circle"], hover_color=theme["mid"], text="",
                command=lambda n=name: self._col_select_theme(n),
            ).place(relx=0.5, rely=0.5, anchor="center")
            self._col_swatch_frames[name] = ring

        ctk.CTkFrame(parent, height=1, fg_color=_DIVIDER).pack(fill="x", padx=18, pady=(14, 0))

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(14, 14))

        ctk.CTkLabel(
            body, text="BRIGHTSPACE UNIT URL",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._col_url_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._col_url_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._col_url_entry)

        ctk.CTkLabel(
            body, text="TARGET PAGE URL  (empty Brightspace page you created)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._col_target_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://learn.okanagancollege.ca/d2l/le/content/…/topics/…/View",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._col_target_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._col_target_entry)

        ctk.CTkLabel(
            body, text="GEMINI API KEY  (leave blank to skip styling)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._col_key_entry = ctk.CTkEntry(
            body, placeholder_text="AIza…",
            height=38, font=ctk.CTkFont(size=13), show="•",
        )
        try:
            from api_config import GEMINI_API_KEY
            self._col_key_entry.insert(0, GEMINI_API_KEY)
        except ImportError:
            pass
        self._col_key_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._col_key_entry)

        par_row = ctk.CTkFrame(body, fg_color="transparent")
        par_row.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(
            par_row, text="PARALLEL PAGES  (how many topics to scrape at once)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(side="left")
        self._col_parallel_entry = ctk.CTkEntry(
            par_row, width=52, height=32, font=ctk.CTkFont(size=13),
        )
        self._col_parallel_entry.insert(0, "3")
        self._col_parallel_entry.pack(side="right")

        self._col_run_btn = ctk.CTkButton(
            body, text="▶  Collect & Assemble", height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._col_start_run,
        )
        self._col_run_btn.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            body, text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._col_log_box = _make_log_box(body)

    def _col_select_theme(self, name: str):
        if name == self._selected_col_theme:
            return
        self._col_swatch_frames[self._selected_col_theme].configure(border_color=_BG)
        self._selected_col_theme = name
        self._col_swatch_frames[name].configure(border_color="#ffffff")

    def _col_start_run(self):
        unit_url   = self._col_url_entry.get().strip()
        target_url = self._col_target_entry.get().strip()
        api_key    = self._col_key_entry.get().strip()
        try:
            parallel_pages = max(1, min(10, int(self._col_parallel_entry.get().strip())))
        except ValueError:
            parallel_pages = 3

        if not unit_url:
            _log_append(self._col_log_box, "⚠  Paste a Brightspace unit URL first.", "warning")
            return
        if not target_url:
            _log_append(self._col_log_box, "⚠  Paste the target page URL first.", "warning")
            return

        theme_colors = PAGE_THEMES[self._selected_col_theme]

        style_ref_path = Path(__file__).parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._col_run_btn.configure(state="disabled", text="Running…")
        self._col_log_box.configure(state="normal")
        self._col_log_box.delete("1.0", "end")
        self._col_log_box.configure(state="disabled")

        q = self._col_log_queue

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
                from unit_collector import run as collector_run
                asyncio.run(collector_run(
                    unit_url=unit_url,
                    target_url=target_url,
                    theme_name=self._selected_col_theme,
                    theme_colors=theme_colors,
                    gemini_api_key=api_key,
                    style_reference_html=style_reference_html,
                    parallel_pages=parallel_pages,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_complete,
                ))
            except Exception as e:
                q.put((f"✗  {e}", "error"))
            finally:
                sys.stdout = old
                on_complete()

        threading.Thread(target=worker, daemon=True).start()

    def _col_poll_log(self):
        try:
            while True:
                msg, tag = self._col_log_queue.get_nowait()
                if msg == "__DONE__":
                    self._col_run_btn.configure(state="normal", text="▶  Collect & Assemble")
                else:
                    _log_append(self._col_log_box, msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._col_poll_log)

    # ── Style Migrator tab ────────────────────────────────────────────────────

    def _build_style_migrator_tab(self, parent):
        cfg = self._load_config()

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 0))
        ctk.CTkLabel(
            hdr, text="🎨 Style Migrator",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Restyle a Brightspace page to match the layout and design of a Moodle page",
            font=ctk.CTkFont(size=12), text_color=_TEXT_DIM,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkFrame(parent, height=1, fg_color=_DIVIDER).pack(fill="x", padx=18, pady=(14, 0))

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(14, 14))

        # Brightspace URL
        ctk.CTkLabel(
            body, text="BRIGHTSPACE TOPIC URL",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._sm_bs_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://learn.okanagancollege.ca/d2l/le/content/…",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._sm_bs_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._sm_bs_entry)

        # Moodle URL
        ctk.CTkLabel(
            body, text="MOODLE PAGE URL",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._sm_moodle_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://moodle.example.com/course/view.php?id=…",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._sm_moodle_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._sm_moodle_entry)

        # Color + API key (side by side)
        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 12))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=3)

        color_col = ctk.CTkFrame(row2, fg_color="transparent")
        color_col.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkLabel(
            color_col, text="PRIMARY COLOR",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._sm_color_entry = ctk.CTkEntry(
            color_col, placeholder_text="#2D8CFF",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._sm_color_entry.insert(0, cfg.get("primary_color", "#2D8CFF"))
        self._sm_color_entry.pack(fill="x")
        self._bind_paste_menu(self._sm_color_entry)

        key_col = ctk.CTkFrame(row2, fg_color="transparent")
        key_col.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            key_col, text="GEMINI API KEY",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._sm_key_entry = ctk.CTkEntry(
            key_col, placeholder_text="AIza…",
            height=38, font=ctk.CTkFont(size=13), show="•",
        )
        saved_key = cfg.get("gemini_api_key", "")
        if not saved_key:
            try:
                from api_config import GEMINI_API_KEY
                saved_key = GEMINI_API_KEY
            except ImportError:
                pass
        if saved_key:
            self._sm_key_entry.insert(0, saved_key)
        self._sm_key_entry.pack(fill="x")
        self._bind_paste_menu(self._sm_key_entry)

        # Run button
        self._sm_run_btn = ctk.CTkButton(
            body, text="▶  Run Migration",
            height=42, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._sm_start_run,
        )
        self._sm_run_btn.pack(fill="x", pady=(0, 12))

        # Log
        ctk.CTkLabel(
            body, text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._sm_log_box = _make_log_box(body)

    def _sm_start_run(self):
        bs_url     = self._sm_bs_entry.get().strip()
        moodle_url = self._sm_moodle_entry.get().strip()
        color      = self._sm_color_entry.get().strip() or "#2D8CFF"
        api_key    = self._sm_key_entry.get().strip()

        if not bs_url:
            _log_append(self._sm_log_box, "⚠  Paste a Brightspace URL first.", "warning")
            return
        if not moodle_url:
            _log_append(self._sm_log_box, "⚠  Paste a Moodle URL first.", "warning")
            return

        self._save_config({"gemini_api_key": api_key, "primary_color": color})

        self._sm_run_btn.configure(state="disabled", text="Running…")
        self._sm_log_box.configure(state="normal")
        self._sm_log_box.delete("1.0", "end")
        self._sm_log_box.configure(state="disabled")

        q = self._sm_log_queue

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
                from style_migrator import StyleMigrator
                asyncio.run(StyleMigrator(
                    brightspace_url=bs_url,
                    moodle_url=moodle_url,
                    primary_color=color,
                    gemini_api_key=api_key,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_complete,
                ).run())
            except Exception as e:
                q.put((f"✗  {e}", "error"))
            finally:
                sys.stdout = old
                on_complete()

        threading.Thread(target=worker, daemon=True).start()

    def _sm_poll_log(self):
        try:
            while True:
                msg, tag = self._sm_log_queue.get_nowait()
                if msg == "__DONE__":
                    self._sm_run_btn.configure(state="normal", text="▶  Run Migration")
                else:
                    _log_append(self._sm_log_box, msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._sm_poll_log)

    # ── Shared helpers ────────────────────────────────────────────────────────

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

    def _load_config(self) -> dict:
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self, data: dict) -> None:
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    App().mainloop()