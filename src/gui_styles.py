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

QCheckBox {{ color: {c['TEXT_PRI']}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {c['BORDER_ACT']};
    border-radius: 3px; background-color: {c['PANEL']};
}}
QCheckBox::indicator:checked {{ background-color: {c['OC_TEAL']}; border-color: {c['OC_TEAL']}; }}

QPushButton {{
    background-color: {c['OC_TEAL']}; color: #ffffff; border: none;
    border-radius: 6px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover {{ background-color: {c['OC_TEAL_MID']}; }}
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
    background-color: {c['OC_TEAL']}; color: #ffffff; border: none;
    border-radius: 6px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}}
QToolButton:hover {{ background-color: {c['OC_TEAL_MID']}; }}
QToolButton:disabled {{ background-color: {c['BTN_DISABLED_BG']}; color: {c['TEXT_SEC']}; }}
QToolButton::menu-button {{
    border: none; border-left: 1px solid {c['OC_TEAL_MID']};
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
    width: 26px;
}}
QToolButton::menu-arrow {{
    width: 10px; height: 10px;
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
QFrame[role="card"] QLineEdit {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
}}
QFrame[role="card"] QSpinBox {{
    background-color: {c['BG']}; border: 1px solid {c['BORDER']}; border-radius: 6px;
    padding: 2px 4px; color: {c['TEXT_PRI']};
}}
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
