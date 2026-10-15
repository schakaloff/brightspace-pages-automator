"""Structured Transfer styles: the content-workspace look, in both app themes."""
import gui_styles
from panels.duplicate_styles import colors


def palette():
    c = dict(colors())
    m = gui_styles.modern_colors()
    c.update(warning=m['warning'], warning_bg=m['warning_bg'], success=m['success'], success_bg=m['success_bg'],
             error=m['error'], soft=m['soft'], hover=m['hover'])
    return c


def transfer_stylesheet():
    c = palette(); s = 'QWidget#transfer_panel'
    tick = gui_styles.modern_asset('check-white.png')
    arrow = gui_styles.modern_asset('arrow-down-light.png' if gui_styles.is_light() else 'arrow-down-dark.png')
    hover = '#007a80' if gui_styles.is_light() else '#a4f5ea'
    return f'''
    {s}, {s} QWidget {{ background: {c['bg']}; color: {c['text']}; font-family: "Segoe UI"; font-size: 15px; }}
    {s} QLabel {{ border: none; background: transparent; }}
    {s} QLabel[role="heading"] {{ font-size: 30px; font-weight: 700; }}
    {s} QLabel[role="section"] {{ font-size: 20px; font-weight: 600; }}
    {s} QLabel[role="pane-title"] {{ font-size: 13px; font-weight: 600; color: {c['muted']}; }}
    {s} QLabel[role="muted"] {{ color: {c['muted']}; font-size: 14px; }}
    {s} QLabel[role="field"] {{ font-weight: 600; font-size: 14px; }}
    {s} QLabel[role="step"] {{ color: {c['muted']}; font-size: 14px; }}
    {s} QLabel[role="step"][state="current"] {{ color: {c['text']}; font-weight: 700; }}
    {s} QLabel[role="step"][state="done"] {{ color: {c['accent']}; }}
    {s} QLabel[role="dot"] {{ color: {c['border']}; font-size: 14px; }}
    {s} QLabel[role="count"] {{ background: {c['warning_bg']}; color: {c['warning']}; border-radius: 10px; padding: 1px 9px; font-size: 12px; font-weight: 600; }}
    {s} QLabel[role="count"][state="ok"] {{ background: {c['success_bg']}; color: {c['success']}; }}
    {s} QLabel[role="error"] {{ color: {c['error']}; font-size: 14px; }}
    {s} QLabel[role="stat-value"] {{ font-size: 24px; font-weight: 600; }}
    {s} QFrame[role="line"] {{ background: {c['border']}; border: none; max-height: 1px; }}
    {s} QFrame[role="card"] {{ background: {c['field']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    {s} QFrame[role="card"] QLabel, {s} QFrame[role="card"] QCheckBox, {s} QFrame[role="card"] QRadioButton {{ background: transparent; }}
    {s} QFrame[role="card"][selected="true"] {{ border: 2px solid {c['accent']}; }}
    {s} QFrame[role="stat"] {{ background: {c['field']}; border: none; border-radius: 8px; }}
    {s} QFrame[role="stat"] QLabel {{ background: transparent; }}
    {s} QFrame[role="issue"] {{ background: transparent; border: none; border-bottom: 1px solid {c['border']}; }}
    {s} QFrame[role="pane"] {{ background: {c['field']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    {s} QFrame[role="pane"] QWidget {{ background: transparent; }}
    {s} QFrame#transfer_footer {{ background: {c['bg']}; border: none; border-top: 1px solid {c['border']}; }}
    {s} QLineEdit, {s} QComboBox, {s} QPlainTextEdit {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['border']};
        border-radius: 6px; padding: 9px 12px; min-height: 22px; selection-background-color: {c['selected']}; }}
    {s} QLineEdit:focus, {s} QComboBox:focus, {s} QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
    {s} QComboBox::drop-down {{ border: none; width: 28px; background: transparent; }}
    {s} QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 8px; }}
    {s} QComboBox QAbstractItemView {{ background: {c['field']}; color: {c['text']}; selection-background-color: {c['selected']}; border: 1px solid {c['border']}; }}
    {s} QScrollArea {{ background: transparent; border: none; }}
    {s} QTreeWidget, {s} QListWidget {{ background: {c['field']}; border: none; outline: none; selection-color: {c['text']}; }}
    {s} QTreeWidget::item, {s} QListWidget::item {{ padding: 6px 4px; border: none; min-height: 20px; }}
    {s} QTreeWidget::item:selected, {s} QListWidget::item:selected {{ background: {c['selected']}; color: {c['text']}; }}
    {s} QTreeWidget::item:hover, {s} QListWidget::item:hover {{ background: {c['hover']}; }}
    {s} QCheckBox, {s} QRadioButton {{ spacing: 10px; padding: 4px 0; }}
    {s} QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {c['muted']}; border-radius: 3px; background: {c['field']}; }}
    {s} QCheckBox::indicator:checked {{ background: #007a80; border-color: #007a80; image: url("{tick}"); }}
    {s} QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {c['muted']}; border-radius: 9px; background: {c['field']}; }}
    {s} QRadioButton::indicator:checked {{ background: {c['field']}; border: 5px solid #007a80; width: 8px; height: 8px; }}
    {s} QPushButton {{ color: {c['accent']}; background: transparent; border: none; padding: 9px 12px; min-height: 20px; border-radius: 6px; }}
    {s} QPushButton:hover {{ background: {c['selected']}; }}
    {s} QPushButton[variant="secondary"] {{ border: 1px solid {c['border']}; color: {c['text']}; background: {c['field']}; }}
    {s} QPushButton[variant="secondary"]:hover {{ border-color: {c['accent']}; }}
    {s} QPushButton[variant="primary"] {{ background: {c['button']}; color: {c['button_text']}; font-weight: 700; padding: 11px 22px; }}
    {s} QPushButton[variant="primary"]:hover {{ background: {hover}; }}
    {s} QPushButton[variant="step"] {{ color: {c['muted']}; padding: 4px 2px; font-size: 14px; }}
    {s} QPushButton[variant="step"][state="current"] {{ color: {c['text']}; font-weight: 700; }}
    {s} QPushButton[variant="step"][state="done"] {{ color: {c['accent']}; }}
    {s} QPushButton[variant="step"]:disabled {{ color: {c['muted']}; background: transparent; }}
    {s} QPushButton[variant="small"] {{ border: 1px solid {c['border']}; color: {c['text']}; background: {c['field']}; padding: 5px 11px; font-size: 13px; }}
    {s} QPushButton[variant="small"]:hover {{ border-color: {c['accent']}; }}
    {s} QPushButton::menu-indicator {{ image: none; width: 0; }}
    {s} QPushButton:disabled {{ color: {c['muted']}; background: transparent; }}
    {s} QPushButton[variant="primary"]:disabled {{ background: {c['soft']}; color: {c['muted']}; }}
    {s} QMenu {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
    {s} QMenu::item {{ padding: 7px 18px; border-radius: 4px; }}
    {s} QMenu::item:selected {{ background: {c['selected']}; }}
    {s} QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
    {s} QSplitter::handle {{ background: transparent; }}
    {s} QProgressBar {{ background: {c['soft']}; border: none; border-radius: 3px; max-height: 6px; }}
    {s} QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
    {s} QPlainTextEdit#transfer_text {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; }}
    {s} QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
    {s} QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 28px; border-radius: 3px; }}
    {s} QScrollBar::add-page:vertical, {s} QScrollBar::sub-page:vertical {{ background: transparent; }}
    '''
