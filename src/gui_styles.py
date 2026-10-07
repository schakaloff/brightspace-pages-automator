# ── Color palettes ────────────────────────────────────────────
DARK = {
    "BG":          "#0d0d12",
    "SIDEBAR":     "#0a0a0f",
    "PANEL":       "#13131b",
    "BORDER":      "#1c1c2a",
    "BORDER_ACT":  "#2a2a3f",
    "TEXT_PRI":    "#dde0ee",
    "TEXT_SEC":    "#636780",
    "TEXT_FAINT":  "#383b50",
    "OC_TEAL":     "#005F63",
    "OC_TEAL_MID": "#007a80",
    "OC_TEAL_BG":  "#002e30",
    "OC_ORANGE":   "#FF8204",
    "DONE":        "#22c55e",
    "RUNNING":     "#f59e0b",
    "LOCKED_BG":   "#252535",
    "ERROR":       "#ef4444",
    "BTN_DISABLED_BG": "#1a2a2c",
    "LOG_INFO":    "#b0bcd4",
    "LOG_SUCCESS": "#4caf50",
    "LOG_ERROR":   "#ef5350",
    "LOG_WARNING": "#f0a500",
    "LOG_STEP":    "#4dd0e1",
    "LOG_DIM":     "#333850",
}

LIGHT = {
    "BG":          "#f5f6fa",
    "SIDEBAR":     "#ecedf3",
    "PANEL":       "#ffffff",
    "BORDER":      "#d0d3e0",
    "BORDER_ACT":  "#b0b5cc",
    "TEXT_PRI":    "#1a1b2e",
    "TEXT_SEC":    "#6b6f87",
    "TEXT_FAINT":  "#9095b0",
    "OC_TEAL":     "#005F63",
    "OC_TEAL_MID": "#007a80",
    "OC_TEAL_BG":  "#cce8e9",
    "OC_ORANGE":   "#FF8204",
    "DONE":        "#16a34a",
    "RUNNING":     "#d97706",
    "LOCKED_BG":   "#e8e9f0",
    "ERROR":       "#dc2626",
    "BTN_DISABLED_BG": "#c5c8d6",
    "LOG_INFO":    "#374151",
    "LOG_SUCCESS": "#15803d",
    "LOG_ERROR":   "#b91c1c",
    "LOG_WARNING": "#b45309",
    "LOG_STEP":    "#0e7490",
    "LOG_DIM":     "#020305",
}

current = dict(DARK)


# Brand orange is only ~2.1:1 against the light theme's surfaces — too weak for
# thin strokes and small text. On light backgrounds a darkened orange keeps the
# hue while staying legible. One definition, used by the sidebar and the update
# badge so they cannot drift apart.
ACCENT_ON_LIGHT = "#b85c00"


def is_light() -> bool:
    """Perceptual lightness of the current background."""
    bg = current.get("BG", "#0d0d12").lstrip("#")
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140


def accent() -> str:
    """Attention colour that stays visible in whichever theme is active."""
    return ACCENT_ON_LIGHT if is_light() else current["OC_ORANGE"]


# The sidebar sits on SIDEBAR, which is darker than BG in dark and lighter than
# BG in light. TEXT_SEC/TEXT_FAINT are tuned against PANEL and fall short there:
# measured 3.56:1 for idle labels and 1.80:1 for section headers. These are
# picked against the sidebar surface specifically.
_SIDEBAR_LABEL = {"dark": "#a2abc4", "light": "#50556b"}
_SIDEBAR_HEADER = {"dark": "#767e9c", "light": "#6a6f86"}
_STATUS_DONE = {"dark": "#22c55e", "light": "#0f7a37"}


def _theme_key() -> str:
    return "light" if is_light() else "dark"


def sidebar_label() -> str:
    """Idle step label — needs 4.5:1 as small bold text."""
    return _SIDEBAR_LABEL[_theme_key()]


def sidebar_header() -> str:
    """Section heading — small caps, needs at least 3:1."""
    return _SIDEBAR_HEADER[_theme_key()]


def status_done() -> str:
    """Done dot; the stock green is only 2.8:1 on the light sidebar."""
    return _STATUS_DONE[_theme_key()]


def check_png_path() -> str:
    """Path to the checkbox tick, rendering it if a QApplication exists.

    Always white: the indicator behind it is filled with OC_TEAL_MID in both
    themes, dark enough for white to read clearly.

    This module builds APP_STYLESHEET at import time, which happens before
    QApplication is constructed. Touching QPixmap that early aborts the process,
    so the render is skipped until there is a GUI app; get_stylesheet() is called
    again after startup, which is when the file actually gets written.
    """
    from pathlib import Path
    import os

    base = (Path(os.environ["APPDATA"]) / "BrightspaceAutomator"
            if os.name == "nt"
            else Path.home() / ".local" / "share" / "BrightspaceAutomator")
    path = base / "check.png"

    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is not None:
            from gui_icons import write_png
            write_png("check", "#ffffff", 16, path)
    except Exception:
        pass

    # QSS urls need forward slashes even on Windows.
    return str(path).replace("\\", "/")


def set_theme(name: str):
    current.update(DARK if name == "dark" else LIGHT)


def get_stylesheet() -> str:
    c = current
    return f"""
QMainWindow, QWidget {{ background-color: {c['BG']}; color: {c['TEXT_PRI']}; font-family: "Segoe UI", system-ui, sans-serif; font-size: 12px; }}
QDialog {{ background-color: {c['BG']}; }}

QWidget#sidebar {{ background-color: {c['SIDEBAR']}; border-right: 1px solid {c['BORDER']}; }}
QWidget#content {{ background-color: {c['BG']}; }}

QPushButton[class="step-btn"] {{
    background-color: transparent; border: none;
    border-left: 3px solid transparent;
    color: {c['TEXT_SEC']}; text-align: left; padding: 0px 12px;
    font-size: 13px; font-weight: 600; border-radius: 0px;
}}
QPushButton[class="step-btn"]:hover {{ background-color: {c['SIDEBAR']}; color: {c['TEXT_PRI']}; }}
QPushButton[class="step-btn"][active="true"] {{
    background-color: {c['PANEL']}; border-left: 3px solid {c['OC_TEAL']}; color: {c['TEXT_PRI']};
}}

QLineEdit {{
    background-color: {c['PANEL']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
    color: {c['TEXT_PRI']}; padding: 8px 12px; font-size: 13px;
    selection-background-color: {c['OC_TEAL']};
}}
QLineEdit:focus {{ border: 1px solid {c['BORDER_ACT']}; }}

QTextEdit, QPlainTextEdit {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
    color: {c['TEXT_PRI']}; padding: 8px;
}}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['BORDER_ACT']}; }}

QCheckBox {{ color: {c['TEXT_PRI']}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {c['BORDER_ACT']};
    border-radius: 3px; background-color: {c['PANEL']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['OC_TEAL_MID']}; border-color: {c['OC_TEAL_MID']};
    image: url("{check_png_path()}");
}}
QCheckBox::indicator:hover {{ border-color: {c['OC_TEAL_MID']}; }}

QComboBox, NoScrollComboBox {{
    background-color: {c['PANEL']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
    color: {c['TEXT_PRI']}; padding: 8px 12px; font-size: 13px;
}}
QComboBox:hover, NoScrollComboBox:hover {{ border-color: {c['OC_TEAL_MID']}; }}
QComboBox:focus, NoScrollComboBox:focus {{ border: 1px solid {c['BORDER_ACT']}; }}


QPushButton {{
    background-color: {c['OC_TEAL']}; color: #ffffff; border: 1px solid transparent;
    border-radius: 6px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover {{ background-color: {c['OC_TEAL_MID']}; }}
QPushButton:focus {{ border: 1px solid {c['BORDER_ACT']}; outline: none; }}
QPushButton:disabled {{ background-color: {c['BTN_DISABLED_BG']}; color: {c['TEXT_SEC']}; }}
QPushButton[variant="secondary"] {{
    background-color: transparent; border: 1px solid {c['BORDER_ACT']}; color: {c['TEXT_SEC']};
}}
QPushButton[variant="secondary"]:hover {{ border-color: {c['OC_TEAL']}; color: {c['TEXT_PRI']}; }}
QPushButton[variant="phase-b"] {{ background-color: #4c1d95; }}
QPushButton[variant="phase-b"]:hover {{ background-color: #5b21b6; }}
QPushButton[variant="success"] {{ background-color: #16653a; }}
QPushButton[variant="success"]:hover {{ background-color: #1a7a46; }}
QPushButton[variant="next-step"] {{
    background-color: {c['OC_TEAL_BG']}; border: 1px solid {c['OC_TEAL']}; color: {c['TEXT_PRI']};
}}
QPushButton[variant="next-step"]:hover {{ background-color: {c['OC_TEAL']}; }}
QPushButton[variant="theme-active"] {{
    background-color: {c['OC_TEAL_BG']}; border: 1px solid {c['OC_TEAL']}; color: {c['TEXT_PRI']};
}}

QLabel {{ color: {c['TEXT_PRI']}; font-size: 12px; }}
QLabel[role="header"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="form-label"] {{ font-size: 10px; font-weight: 700; color: {c['TEXT_FAINT']}; }}
QLabel[role="dim"] {{ color: {c['TEXT_SEC']}; }}

QScrollBar:vertical {{
    background: {c['PANEL']}; width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {c['BORDER_ACT']}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{
    background: {c['PANEL']}; height: 6px; border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {c['BORDER_ACT']}; border-radius: 3px; min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QToolButton {{
    background-color: {c['OC_TEAL']}; color: #ffffff; border: 1px solid transparent;
    border-radius: 6px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}}
QToolButton:hover {{ background-color: {c['OC_TEAL_MID']}; }}
QToolButton:focus {{ border: 1px solid {c['BORDER_ACT']}; outline: none; }}
QToolButton:disabled {{ background-color: {c['BTN_DISABLED_BG']}; color: {c['TEXT_SEC']}; }}
QToolButton::menu-button {{
    border: none; border-left: 1px solid {c['OC_TEAL_MID']};
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
    width: 26px;
}}
QToolButton::menu-arrow {{
    width: 10px; height: 10px;
}}

/* The generic QToolButton rule above paints a filled teal pill with 18px of
   horizontal padding. On the 30x30 corner badge that covers the icon entirely
   and shows as a solid square, so reset it to a bare icon with a hover tint. */
QToolButton#update_badge {{
    background-color: transparent; border: none;
    padding: 0px; margin: 0px; border-radius: 15px;
}}
QToolButton#update_badge:hover {{ background-color: {c['LOCKED_BG']}; }}
QToolButton#update_badge:pressed {{ background-color: {c['BORDER_ACT']}; }}

QToolButton#checker_details_toggle {{
    background-color: transparent; color: {c['TEXT_SEC']}; border: none;
    padding: 5px 0px; text-align: left;
}}
QToolButton#checker_details_toggle:hover {{ color: {c['TEXT_PRI']}; }}

QTabWidget#course_workflow_tabs::pane {{
    border: 1px solid {c['BORDER']}; border-radius: 8px;
    background-color: {c['PANEL']};
}}
QTabWidget#course_workflow_tabs QTabBar::tab {{
    color: {c['TEXT_SEC']}; background: transparent; padding: 9px 18px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabWidget#course_workflow_tabs QTabBar::tab:selected {{
    color: {c['TEXT_PRI']}; border-bottom: 2px solid {c['OC_TEAL']};
}}
QListWidget#checker_findings {{
    background-color: {c['BG']}; color: {c['TEXT_PRI']};
    border: 1px solid {c['BORDER']}; border-radius: 6px;
    padding: 4px;
}}

QMenu {{
    background-color: {c['PANEL']}; border: 1px solid {c['BORDER_ACT']};
    color: {c['TEXT_PRI']}; border-radius: 6px; padding: 4px 0px;
}}
QMenu::item {{ padding: 8px 20px; border-radius: 4px; font-size: 12px; }}
QMenu::item:selected {{ background-color: {c['OC_TEAL']}; color: #ffffff; }}
QMenu::item:checked {{ color: {c['DONE']}; }}
QMenu::item:checked:selected {{ color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {c['BORDER']}; margin: 4px 10px; }}

QFrame[role="divider"] {{ background: {c['BORDER']}; max-height: 1px; border: none; }}

QFrame[role="card"] {{
    background-color: {c['PANEL']}; border: 1px solid {c['BORDER']}; border-radius: 10px;
}}
QFrame[role="card"] QWidget {{ background: transparent; }}
QFrame[role="card"] QTextEdit {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
}}
QFrame[role="card"] QLineEdit {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
}}
QFrame[role="card"] QSpinBox {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
    padding: 2px 4px; color: {c['TEXT_PRI']};
}}
QFrame[role="card"] QSpinBox:focus {{ border: 1px solid {c['BORDER_ACT']}; }}
QFrame[role="card"] QSpinBox::up-button, QFrame[role="card"] QSpinBox::down-button {{
    width: 18px; border: none; background: transparent;
}}
QFrame[role="card"] QSpinBox::up-button:hover, QFrame[role="card"] QSpinBox::down-button:hover {{
    background: {c['BORDER']};
}}
QFrame[role="card-accent"] {{
    background-color: {c['OC_TEAL_BG']}; border: 1px solid {c['OC_TEAL']}; border-radius: 8px;
}}
QFrame[role="card-accent"] QWidget {{ background: transparent; }}

QToolTip {{
    background-color: {c['PANEL']};
    color: {c['TEXT_PRI']};
    border: 1px solid {c['BORDER_ACT']};
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: normal;
}}
""" + get_modern_stylesheet()


def modern_colors() -> dict:
    """Shared tokens for screens adopting the roomier desktop design."""
    if is_light():
        return dict(bg="#f4f7fa", surface="#ffffff", border="#dce4ec",
                    text="#1c2b3b", muted="#536477", soft="#edf2f6",
                    selected="#e0f0f0", hover="#f2f7f9", focus="#007a80",
                    success="#166b45", success_bg="#e5f5ec",
                    warning="#88520b", warning_bg="#fff0d4", error="#b42318", disabled="#8a97a6")
    return dict(bg="#10161f", surface="#18212d", border="#2d3a4b",
                text="#edf2f8", muted="#a8b6c9", soft="#253244",
                selected="#183e43", hover="#1e2b3a", focus="#36bac2",
                success="#94dfb5", success_bg="#183d2e",
                warning="#f6d397", warning_bg="#44341d", error="#ffb4a8", disabled="#738297")


def modern_asset(name: str) -> str:
    from pathlib import Path
    import sys
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return str(root / "assets" / "ui" / name).replace("\\", "/")


def get_modern_stylesheet() -> str:
    """Opt-in controls; other screens can adopt design='modern' incrementally."""
    c = modern_colors()
    s = 'QWidget[design="modern"]'
    arrow = modern_asset(f"arrow-down-{'light' if is_light() else 'dark'}.png")
    tick = modern_asset("check-white.png")
    return f"""
{s}, {s} QWidget {{
    background-color: transparent; color: {c['text']}; font-size: 14px;
}}
{s} {{ background-color: {c['bg']}; }}
QDialog[design="modern"], {s} QDialog[design="modern"] {{ background-color: {c['bg']}; }}
{s} QScrollArea, {s} QScrollArea > QWidget {{ border: none; background: transparent; }}
{s} QFrame[role="surface"] {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
}}
{s} QLabel {{ background: transparent; border: none; }}
{s} QLabel[role="page-heading"] {{ font-size: 26px; font-weight: 700; }}
{s} QLabel[role="section-heading"] {{ font-size: 16px; font-weight: 600; }}
{s} QLabel[role="field-label"] {{ font-size: 13px; font-weight: 600; }}
{s} QLabel[role="dim"], {s} QLabel[role="hint"] {{ color: {c['muted']}; font-size: 13px; }}
{s} QLabel[role="hint"] {{ font-size: 12px; }}
QWidget#checker_panel QTabWidget#course_workflow_tabs::pane {{ border: none; background: transparent; }}
QWidget#checker_panel QTabWidget#course_workflow_tabs QTabBar::tab {{
    background: transparent; color: {c['muted']}; border: none; border-radius: 7px;
    margin: 0 6px 10px 0; padding: 10px 20px; font-size: 13px; font-weight: 600;
}}
QWidget#checker_panel QTabWidget#course_workflow_tabs QTabBar::tab:selected {{
    background: {c['selected']}; color: {c['focus']};
}}
QWidget#checker_panel QTabWidget#course_workflow_tabs QTabBar::tab:hover {{ background: {c['soft']}; }}
QWidget#checker_panel QListWidget#checker_findings {{
    background: {c['surface']}; border: none; color: {c['text']}; font-size: 13px; outline: none;
}}
QWidget#checker_panel QListWidget#checker_findings::item {{
    padding: 9px 8px; border-bottom: 1px solid {c['soft']};
}}
QWidget#checker_panel QListWidget#checker_findings::item:selected {{
    background: {c['selected']}; color: {c['text']};
}}
QWidget#checker_panel QFrame[role="checker-fix-row"] {{ border: none; border-bottom: 1px solid {c['border']}; }}
QWidget#checker_panel QCheckBox {{ font-size: 14px; font-weight: 600; }}
QWidget#checker_panel QCheckBox:disabled {{ color: {c['disabled']}; }}
QWidget#checker_panel QToolButton#checker_details_toggle {{ color: {c['muted']}; }}
QWidget#checker_panel QToolButton#checker_details_toggle:hover {{ color: {c['text']}; background: {c['soft']}; }}
QWidget#build_style_hub QLabel[role="hub-section"] {{ font-size: 20px; font-weight: 700; }}
QWidget#build_style_hub QLabel[role="hub-intro"] {{ color: {c['muted']}; font-size: 14px; }}
QWidget#build_style_hub QLabel[role="hub-tool-heading"] {{ font-size: 17px; font-weight: 600; }}
QWidget#build_style_hub QLabel[role="hub-description"] {{ color: {c['muted']}; font-size: 14px; }}
QWidget#build_style_hub QPushButton[variant="hub-outline"] {{
    background: {c['surface']}; color: {c['focus']}; border-color: {c['focus']};
}}
QWidget#build_style_hub QPushButton[variant="hub-outline"]:hover {{ background: {c['selected']}; }}
{s} QLineEdit, {s} QPlainTextEdit, {s} QTextBrowser {{
    background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 10px 12px; font-size: 14px;
    selection-background-color: {c['selected']}; selection-color: {c['text']};
}}
{s} QLineEdit {{ min-height: 20px; }}
{s} QLineEdit:focus, {s} QPlainTextEdit:focus {{ border-color: {c['focus']}; }}
{s} QLineEdit:read-only {{ background: {c['soft']}; }}
{s} QComboBox {{
    background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 10px 36px 10px 12px; font-size: 14px; min-height: 20px;
}}
{s} QComboBox:hover, {s} QComboBox:focus {{ border-color: {c['focus']}; }}
{s} QComboBox::drop-down {{ border: none; width: 32px; background: transparent; }}
{s} QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
{s} QComboBox QAbstractItemView {{
    background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
    padding: 6px; selection-background-color: {c['selected']}; selection-color: {c['text']};
    outline: none;
}}
{s} QComboBox QAbstractItemView::item {{ min-height: 32px; padding: 4px 8px; }}
{s} QPushButton, {s} QToolButton {{
    background: #005f63; color: #ffffff; border: 1px solid transparent;
    border-radius: 8px; padding: 9px 14px; font-size: 13px; font-weight: 600; min-height: 20px;
}}
{s} QPushButton:hover {{ background: #007a80; }}
{s} QPushButton[variant="secondary"] {{
    background: {c['surface']}; color: {c['text']}; border-color: {c['border']};
}}
{s} QPushButton[variant="secondary"]:hover {{ background: {c['hover']}; border-color: {c['focus']}; }}
{s} QPushButton[variant="ghost"], {s} QToolButton[variant="ghost"] {{
    color: {c['muted']}; background: transparent; border: none; padding: 4px 8px; min-height: 20px;
}}
{s} QPushButton[variant="ghost"]:hover, {s} QToolButton[variant="ghost"]:hover {{
    color: {c['text']}; background: {c['soft']};
}}
{s} QPushButton:focus, {s} QToolButton:focus {{ border: 1px solid {c['focus']}; }}
{s} QPushButton:disabled, {s} QPushButton[variant="secondary"]:disabled,
{s} QPushButton[variant="ghost"]:disabled {{
    background: {c['soft']}; color: {c['disabled']}; border-color: transparent;
}}
{s} QComboBox:disabled {{ background: {c['soft']}; color: {c['disabled']}; }}
{s} QCheckBox {{ color: {c['text']}; font-size: 13px; spacing: 10px; }}
{s} QCheckBox::indicator {{ width: 18px; height: 18px; border-color: {c['border']}; border-radius: 5px; }}
{s} QCheckBox::indicator:checked {{ background: #007a80; border-color: #007a80; image: url("{tick}"); }}
{s} QTableWidget {{
    background: {c['surface']}; color: {c['text']}; border: none; font-size: 14px;
    selection-background-color: {c['selected']}; selection-color: {c['text']}; outline: none;
}}
{s} QTableWidget::item {{ padding: 6px 8px; border: none; border-bottom: 1px solid {c['soft']}; }}
{s} QTableWidget::item:hover {{ background: {c['hover']}; }}
{s} QTableWidget::item:selected {{ background: {c['selected']}; color: {c['text']}; }}
{s} QHeaderView {{ background: {c['surface']}; }}
{s} QHeaderView::section {{
    background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px; font-size: 12px; font-weight: 600;
}}
{s} QTableCornerButton::section {{ background: {c['surface']}; border: none; }}
{s} QLabel[role="status"] {{
    background: {c['soft']}; color: {c['muted']}; border-radius: 9px;
    padding: 4px 8px; font-size: 11px; font-weight: 600;
}}
{s} QLabel[role="status"][state="created"] {{ background: {c['success_bg']}; color: {c['success']}; }}
{s} QLabel[role="status"][state="review"] {{ background: {c['warning_bg']}; color: {c['warning']}; }}
{s} QLabel#page_creator_activity_summary[state="error"] {{ color: {c['error']}; }}
{s} QLabel#page_creator_activity_summary[state="warning"] {{ color: {c['warning']}; }}
{s} QSplitter::handle {{ background: transparent; width: 12px; }}
{s} QSplitter::handle:hover {{ background: {c['soft']}; border-radius: 4px; }}
{s} QProgressBar {{ border: none; background: {c['soft']}; border-radius: 3px; max-height: 6px; }}
{s} QProgressBar::chunk {{ background: #007a80; border-radius: 3px; }}
{s} QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
{s} QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 28px; border-radius: 3px; }}
{s} QScrollBar::add-page:vertical, {s} QScrollBar::sub-page:vertical {{ background: transparent; }}
{s} QToolButton::menu-indicator {{ image: none; }}
"""


# ── Backward-compat aliases (static, safe for non-dynamic use) ─
BG          = DARK["BG"]
SIDEBAR     = DARK["SIDEBAR"]
PANEL       = DARK["PANEL"]
BORDER      = DARK["BORDER"]
BORDER_ACT  = DARK["BORDER_ACT"]
TEXT_PRI    = DARK["TEXT_PRI"]
TEXT_SEC    = DARK["TEXT_SEC"]
TEXT_FAINT  = DARK["TEXT_FAINT"]
OC_TEAL     = DARK["OC_TEAL"]
OC_TEAL_MID = DARK["OC_TEAL_MID"]
OC_TEAL_BG  = DARK["OC_TEAL_BG"]
OC_ORANGE   = DARK["OC_ORANGE"]
DONE        = DARK["DONE"]
RUNNING     = DARK["RUNNING"]
LOCKED_BG   = DARK["LOCKED_BG"]
ERROR       = DARK["ERROR"]
LOG_INFO    = DARK["LOG_INFO"]
LOG_SUCCESS = DARK["LOG_SUCCESS"]
LOG_ERROR   = DARK["LOG_ERROR"]
LOG_WARNING = DARK["LOG_WARNING"]
LOG_STEP    = DARK["LOG_STEP"]
LOG_DIM     = DARK["LOG_DIM"]

# Keep APP_STYLESHEET for any legacy references
APP_STYLESHEET = get_stylesheet()
