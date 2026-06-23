# ── Colors ────────────────────────────────────────────────────
BG          = "#0d0d12"
SIDEBAR     = "#0a0a0f"
PANEL       = "#13131b"
BORDER      = "#1c1c2a"
BORDER_ACT  = "#2a2a3f"

TEXT_PRI    = "#dde0ee"
TEXT_SEC    = "#636780"
TEXT_FAINT  = "#383b50"

OC_TEAL     = "#005F63"
OC_TEAL_MID = "#007a80"
OC_TEAL_BG  = "#002e30"
OC_ORANGE   = "#FF8204"

DONE        = "#22c55e"
RUNNING     = "#f59e0b"
LOCKED_BG   = "#252535"
ERROR       = "#ef4444"

LOG_INFO    = "#b0bcd4"
LOG_SUCCESS = "#4caf50"
LOG_ERROR   = "#ef5350"
LOG_WARNING = "#f0a500"
LOG_STEP    = "#4dd0e1"
LOG_DIM     = "#333850"

# ── QSS ──────────────────────────────────────────────────────
APP_STYLESHEET = f"""
QMainWindow, QWidget {{ background-color: {BG}; color: {TEXT_PRI}; font-family: "Segoe UI", system-ui, sans-serif; font-size: 12px; }}
QDialog {{ background-color: {BG}; }}

QWidget#sidebar {{ background-color: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QWidget#content {{ background-color: {BG}; }}

QPushButton[class="step-btn"] {{
    background-color: transparent; border: none;
    border-left: 3px solid transparent;
    color: {TEXT_SEC}; text-align: left; padding: 0px 12px;
    font-size: 13px; font-weight: 600; border-radius: 0px;
}}
QPushButton[class="step-btn"]:hover {{ background-color: #18182a; color: {TEXT_PRI}; }}
QPushButton[class="step-btn"][active="true"] {{
    background-color: {PANEL}; border-left: 3px solid {OC_TEAL}; color: {TEXT_PRI};
}}

QLineEdit {{
    background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
    color: {TEXT_PRI}; padding: 8px 12px; font-size: 13px;
    selection-background-color: {OC_TEAL};
}}
QLineEdit:focus {{ border: 1px solid {BORDER_ACT}; }}

QCheckBox {{ color: {TEXT_PRI}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER_ACT};
    border-radius: 3px; background-color: {PANEL};
}}
QCheckBox::indicator:checked {{ background-color: {OC_TEAL}; border-color: {OC_TEAL}; }}

QPushButton {{
    background-color: {OC_TEAL}; color: #ffffff; border: none;
    border-radius: 6px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover {{ background-color: {OC_TEAL_MID}; }}
QPushButton:disabled {{ background-color: #1a2a2c; color: {TEXT_SEC}; }}
QPushButton[variant="secondary"] {{
    background-color: transparent; border: 1px solid {BORDER_ACT}; color: {TEXT_SEC};
}}
QPushButton[variant="secondary"]:hover {{ border-color: {OC_TEAL}; color: {TEXT_PRI}; }}
QPushButton[variant="phase-b"] {{ background-color: #4c1d95; }}
QPushButton[variant="phase-b"]:hover {{ background-color: #5b21b6; }}
QPushButton[variant="success"] {{ background-color: #16653a; }}
QPushButton[variant="success"]:hover {{ background-color: #1a7a46; }}
QPushButton[variant="next-step"] {{
    background-color: {OC_TEAL_BG}; border: 1px solid {OC_TEAL}; color: {TEXT_PRI};
}}
QPushButton[variant="next-step"]:hover {{ background-color: {OC_TEAL}; }}

QLabel {{ color: {TEXT_PRI}; font-size: 12px; }}
QLabel[role="header"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="form-label"] {{ font-size: 10px; font-weight: 700; color: {TEXT_FAINT}; }}
QLabel[role="dim"] {{ color: {TEXT_SEC}; }}

QScrollBar:vertical {{
    background: {PANEL}; width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_ACT}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{
    background: {PANEL}; height: 6px; border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_ACT}; border-radius: 3px; min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""
