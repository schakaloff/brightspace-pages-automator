"""
Brightspace Pages Automator
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
from icon_art import draw_app_icon
from update_checker import check_for_update, download_asset

try:
    from CTkMessagebox import CTkMessagebox
except ImportError:
    CTkMessagebox = None

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Version ──────────────────────────────────────────────────────────────────
VERSION = "0.6.0"  # bump manually per release; CI tag adds the leading "v"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


# ── Config persistence ────────────────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent / "user_config.json"

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


def _make_log_box_grid(parent, row: int, col: int = 0) -> ctk.CTkTextbox:
    """Like _make_log_box but places the border frame with grid so expand works."""
    border = ctk.CTkFrame(parent, fg_color=_LOG_BORDER, corner_radius=8)
    border.grid(row=row, column=col, sticky="nsew")
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
        self.title("Brightspace Pages Automator")
        self.geometry("900x800")
        self.minsize(700, 620)
        self.configure(fg_color=_BG)
        self._set_window_icon()

        self._log_queue              = queue.Queue()
        self._col_log_queue          = queue.Queue()
        self._chk_log_queue          = queue.Queue()
        self._response_queue         = queue.Queue()
        self._selected_theme         = "blue"
        self._swatch_frames          = {}
        self._selected_col_theme     = "blue"
        self._col_swatch_frames      = {}
        self._chk_moodle_ready_event = None
        self._build_ui()
        self.after(100, self._poll_log)
        self.after(100, self._col_poll_log)
        self.after(100, self._chk_poll_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._chromium_ready = False
        self._chromium_dialog = None
        self._chromium_dialog_log = None
        self._chromium_queue = queue.Queue()
        self.after(200, self._chromium_poll)
        threading.Thread(target=self._chromium_check_worker, daemon=True).start()

        self._update_dialog = None
        self._update_queue = queue.Queue()
        self.after(300, self._update_poll)
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    # ── Top-level UI ──────────────────────────────────────────────────────────

    def _set_window_icon(self):
        try:
            from PIL import Image
            import io, tkinter as tk
            img = draw_app_icon(64)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            photo = tk.PhotoImage(data=buf.getvalue())
            self.iconphoto(True, photo)
            self._icon_photo = photo  # prevent GC
        except Exception:
            pass

    # ── Chromium first-run setup ─────────────────────────────────────────────

    def _chromium_check_worker(self):
        from chromium_setup import is_chromium_installed, install_chromium
        if is_chromium_installed():
            self._chromium_queue.put(("__READY__", None))
            return
        self._chromium_queue.put(("__NEED_INSTALL__", None))
        ok, err = install_chromium(
            progress_cb=lambda line: self._chromium_queue.put(("__PROGRESS__", line))
        )
        self._chromium_queue.put(("__INSTALL_DONE__", (ok, err)))

    def _show_chromium_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Setting up browser engine")
        dialog.geometry("480x320")
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)  # can't be cancelled mid-download
        dialog.configure(fg_color=_BG)

        ctk.CTkLabel(
            dialog, text="Downloading browser engine (one-time setup)…",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(20, 8), padx=24, anchor="w")

        log_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        log_frame.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        log_box = _make_log_box(log_frame)

        self._chromium_dialog = dialog
        self._chromium_dialog_log = log_box

    def _chromium_poll(self):
        try:
            while True:
                kind, payload = self._chromium_queue.get_nowait()
                if kind == "__READY__":
                    self._chromium_ready = True
                elif kind == "__NEED_INSTALL__":
                    self._show_chromium_dialog()
                elif kind == "__PROGRESS__":
                    if self._chromium_dialog_log:
                        _log_append(self._chromium_dialog_log, payload, "info")
                elif kind == "__INSTALL_DONE__":
                    ok, err = payload
                    if self._chromium_dialog:
                        self._chromium_dialog.destroy()
                        self._chromium_dialog = None
                        self._chromium_dialog_log = None
                    if ok:
                        self._chromium_ready = True
                    elif CTkMessagebox:
                        CTkMessagebox(
                            title="Chromium setup failed",
                            message=f"Could not download the browser engine:\n{err}\n\n"
                                    "Check your internet connection and restart the app to retry.",
                            icon="cancel",
                        )
        except queue.Empty:
            pass
        self.after(150, self._chromium_poll)

    # ── Self-update check ────────────────────────────────────────────────────

    def _any_job_running(self) -> bool:
        for btn in (self._run_btn, self._col_run_btn, self._chk_run_btn):
            if btn.cget("state") == "disabled":
                return True
        return False

    def _persist_config(self) -> None:
        self._save_config({
            "automator_url":      self._url_entry.get().strip(),
            "pc_gemini_api_key":  self._pc_key_entry.get().strip(),
            "chk_bs_url":         self._chk_bs_entry.get().strip(),
            "chk_moodle_url":     self._chk_moodle_entry.get().strip(),
        })

    def _update_check_worker(self):
        release = check_for_update()
        if not release:
            return
        if self._load_config().get("skipped_update_tag") == release["tag"]:
            return
        self._update_queue.put(release)

    def _update_poll(self):
        try:
            release = self._update_queue.get_nowait()
            self._show_update_dialog(release)
        except queue.Empty:
            pass
        self.after(2000, self._update_poll)

    def _show_update_dialog(self, release: dict):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Update available")
        dialog.geometry("520x420")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.configure(fg_color=_BG)
        self._update_dialog = dialog

        ctk.CTkLabel(
            dialog, text=f"New version available: {release['tag']}",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 8), padx=24, anchor="w")

        notes_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        notes_frame.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        notes_box = _make_log_box(notes_frame)
        notes_box.configure(state="normal")
        notes_box._textbox.insert("end", release["body"])
        notes_box.configure(state="disabled")

        status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11), text_color=_TEXT_FAINT)
        status_label.pack(padx=24, anchor="w")

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(0, 20))
        btn_row.columnconfigure((0, 1, 2), weight=1)

        def do_skip():
            self._save_config({"skipped_update_tag": release["tag"]})
            dialog.destroy()

        def do_later():
            dialog.destroy()

        def do_update():
            if self._any_job_running():
                status_label.configure(
                    text="⚠  Finish your current job before updating.", text_color=_TAG_COLORS["warning"]
                )
                return
            update_btn.configure(state="disabled", text="Updating…")
            skip_btn.configure(state="disabled")
            later_btn.configure(state="disabled")
            threading.Thread(target=self._run_update, args=(release, status_label), daemon=True).start()

        skip_btn = ctk.CTkButton(btn_row, text="Skip this version", fg_color="transparent",
                                  border_width=1, command=do_skip)
        skip_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        later_btn = ctk.CTkButton(btn_row, text="Remind me later", fg_color="transparent",
                                   border_width=1, command=do_later)
        later_btn.grid(row=0, column=1, sticky="ew", padx=6)
        update_btn = ctk.CTkButton(btn_row, text="Update Now", command=do_update)
        update_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        if not release.get("asset_url") and sys.platform == "win32":
            update_btn.configure(state="disabled")
            status_label.configure(text="No installer found in this release.")

    def _run_update(self, release: dict, status_label) -> None:
        import tempfile, webbrowser

        def set_status(text):
            self.after(0, lambda: status_label.configure(text=text))

        if sys.platform != "win32" or not release.get("asset_url"):
            webbrowser.open(release.get("html_url") or "")
            self.after(0, lambda: self._update_dialog and self._update_dialog.destroy())
            return

        try:
            set_status("Downloading update…")
            tmp_dir = Path(tempfile.gettempdir())
            installer_path = tmp_dir / release["asset_name"]
            download_asset(
                release["asset_url"], installer_path,
                progress_cb=lambda pct: set_status(f"Downloading update… {pct}%"),
            )
            set_status("Installing…")
            self.after(0, self._persist_config)
            import subprocess
            subprocess.Popen(
                [str(installer_path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                close_fds=True,
            )
            self.after(0, self.destroy)
        except Exception as e:
            set_status(f"⚠  Update failed: {e}")

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
            hbar, text=f"v{VERSION}",
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
        self._build_checker_tab(tabview.add("✅ Checker"))

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
            body, text="GEMINI API KEY",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).pack(anchor="w", pady=(0, 4))
        self._pc_key_entry = ctk.CTkEntry(
            body, placeholder_text="AIza…",
            height=38, font=ctk.CTkFont(size=13), show="•",
        )
        try:
            from api_config import GEMINI_API_KEY as _key
            self._pc_key_entry.insert(0, _key)
        except ImportError:
            saved_key = self._load_config().get("pc_gemini_api_key", "")
            if saved_key:
                self._pc_key_entry.insert(0, saved_key)
        self._pc_key_entry.pack(fill="x", pady=(0, 12))
        self._bind_paste_menu(self._pc_key_entry)

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
        saved_automator_url = self._load_config().get("automator_url", "")
        if saved_automator_url:
            self._url_entry.insert(0, saved_automator_url)
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
        if not self._chromium_ready:
            _log_append(self._log_box, "⚠  Browser engine still installing… please wait.", "warning")
            return
        url = self._url_entry.get().strip()
        if not url:
            _log_append(self._log_box, "⚠  Paste a Brightspace URL first.", "warning")
            return

        gemini_api_key = self._pc_key_entry.get().strip()

        style_ref_path = _resource_path("templates", "style_reference.html")
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
        if not self._chromium_ready:
            _log_append(self._col_log_box, "⚠  Browser engine still installing… please wait.", "warning")
            return
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

        style_ref_path = _resource_path("templates", "style_reference.html")
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

    # ── Checker tab ───────────────────────────────────────────────────────────

    def _build_checker_tab(self, parent):
        cfg = self._load_config()

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 0))
        ctk.CTkLabel(
            hdr, text="✅ Content Checker",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Verify that Moodle content exists in Brightspace  —  leave either URL blank to test just that side",
            font=ctk.CTkFont(size=12), text_color=_TEXT_DIM,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkFrame(parent, height=1, fg_color=_DIVIDER).pack(fill="x", padx=18, pady=(14, 0))

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(14, 14))
        # Row 7 (log box) gets all extra vertical space
        body.grid_rowconfigure(7, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # Brightspace URL
        ctk.CTkLabel(
            body, text="BRIGHTSPACE COURSE URL  (optional — blank = Moodle only)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._chk_bs_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://learn.okanagancollege.ca/d2l/le/content/<id>/home",
            height=38, font=ctk.CTkFont(size=13),
        )
        if cfg.get("chk_bs_url"):
            self._chk_bs_entry.insert(0, cfg["chk_bs_url"])
        self._chk_bs_entry.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self._bind_paste_menu(self._chk_bs_entry)

        # Moodle URL
        ctk.CTkLabel(
            body, text="MOODLE COURSE URL  (optional — blank = Brightspace only)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).grid(row=2, column=0, sticky="w", pady=(0, 4))
        self._chk_moodle_entry = ctk.CTkEntry(
            body,
            placeholder_text="https://mymoodle.okanagan.bc.ca/course/view.php?id=…",
            height=38, font=ctk.CTkFont(size=13),
        )
        if cfg.get("chk_moodle_url"):
            self._chk_moodle_entry.insert(0, cfg["chk_moodle_url"])
        self._chk_moodle_entry.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self._bind_paste_menu(self._chk_moodle_entry)

        # Run button
        self._chk_run_btn = ctk.CTkButton(
            body, text="▶  Run Check",
            height=42, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._chk_start_run,
        )
        self._chk_run_btn.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        # Ready button container
        self._chk_ready_container = ctk.CTkFrame(body, fg_color="transparent")
        self._chk_ready_container.grid(row=5, column=0, sticky="ew")
        self._chk_ready_btn = ctk.CTkButton(
            self._chk_ready_container, text="✅  Ready — Scrape Now",
            height=38, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#16A34A", hover_color="#15803D",
            command=self._chk_moodle_ready,
        )

        # Log
        ctk.CTkLabel(
            body, text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_TEXT_FAINT,
        ).grid(row=6, column=0, sticky="w", pady=(8, 4))
        self._chk_log_box = _make_log_box_grid(body, row=7)

    def _chk_start_run(self):
        bs_url     = self._chk_bs_entry.get().strip()
        moodle_url = self._chk_moodle_entry.get().strip()

        if not bs_url and not moodle_url:
            _log_append(self._chk_log_box, "⚠  Paste at least one URL.", "warning")
            return

        self._save_config({"chk_bs_url": bs_url, "chk_moodle_url": moodle_url})

        import threading as _threading
        ready_event = _threading.Event()
        self._chk_moodle_ready_event = ready_event
        self._chk_ready_btn.pack_forget()

        self._chk_run_btn.configure(state="disabled", text="Running…")
        self._chk_log_box.configure(state="normal")
        self._chk_log_box.delete("1.0", "end")
        self._chk_log_box.configure(state="disabled")

        q = self._chk_log_queue

        def worker():
            class _Capture:
                def write(self, t):
                    if t.strip(): q.put((t.rstrip(), "dim"))
                def flush(self): pass

            old, sys.stdout = sys.stdout, _Capture()
            done_sent = [False]

            def on_complete():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))

            def on_moodle_waiting():
                q.put(("__CHK_MOODLE_WAITING__", ""))

            try:
                from content_checker import ContentChecker
                asyncio.run(ContentChecker(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_complete,
                    moodle_ready_event=ready_event,
                    on_moodle_waiting=on_moodle_waiting,
                ).run())
            except Exception as e:
                q.put((f"✗  {e}", "error"))
            finally:
                sys.stdout = old
                on_complete()

        threading.Thread(target=worker, daemon=True).start()

    def _chk_poll_log(self):
        try:
            while True:
                msg, tag = self._chk_log_queue.get_nowait()
                if msg == "__DONE__":
                    self._chk_run_btn.configure(state="normal", text="▶  Run Check")
                    self._chk_ready_btn.pack_forget()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._chk_ready_btn.pack(fill="x", pady=(0, 8))
                else:
                    _log_append(self._chk_log_box, msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._chk_poll_log)

    def _chk_moodle_ready(self):
        self._chk_ready_btn.pack_forget()
        if self._chk_moodle_ready_event:
            self._chk_moodle_ready_event.set()

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
            existing = self._load_config()
            existing.update(data)
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[config] save failed: {e}", flush=True)

    def _on_close(self) -> None:
        self._persist_config()
        self.destroy()


if __name__ == "__main__":
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    App().mainloop()