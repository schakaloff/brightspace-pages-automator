"""Scoped Settings styles using the app's existing modern colour tokens."""
import gui_styles


def settings_stylesheet():
    c = gui_styles.modern_colors()
    return f'''
    QWidget#settings_panel {{ background: {c['bg']}; }}
    QWidget#settings_panel QLabel {{ font-size: 14px; }}
    QWidget#settings_panel QLabel[role="hint"] {{ color: {c['muted']}; font-size: 13px; }}
    QWidget#settings_panel QLabel[role="dim"] {{ color: {c['muted']}; font-size: 14px; }}
    QWidget#settings_panel QLabel[role="field-label"] {{ font-size: 14px; font-weight: 600; }}
    QWidget#settings_panel QLineEdit, QWidget#settings_panel QComboBox {{ font-size: 15px; }}
    QWidget#settings_panel QPushButton {{ font-size: 14px; }}
    QWidget#settings_panel QLabel[role="page-heading"] {{ font-size: 28px; font-weight: 700; }}
    QWidget#settings_panel QLabel[role="section-heading"] {{ font-size: 20px; font-weight: 600; }}
    QWidget#settings_panel QTabWidget::pane {{ border: none; }}
    QWidget#settings_panel QTabBar::tab {{
        background: transparent; color: {c['muted']}; border: none;
        border-bottom: 2px solid transparent; padding: 12px 16px;
        font-size: 15px; font-weight: 500;
    }}
    QWidget#settings_panel QTabBar::tab:selected {{ color: {c['focus']}; border-bottom-color: {c['focus']}; }}
    QWidget#settings_panel QTabBar::tab:hover {{ background: {c['hover']}; }}
    QWidget#settings_panel QTabBar::tab:focus {{ background: {c['selected']}; }}
    QWidget#settings_panel QFrame[role="settings-divider"] {{ background: {c['border']}; border: none; }}
    QWidget#settings_panel QRadioButton {{ color: {c['text']}; font-size: 14px; padding: 10px 12px; spacing: 10px; }}
    QWidget#settings_panel QRadioButton:checked {{ background: {c['selected']}; border-radius: 8px; }}
    QWidget#settings_panel QRadioButton:focus {{ border: 1px solid {c['focus']}; border-radius: 8px; }}
    QWidget#settings_panel QRadioButton::indicator {{
        width: 16px; height: 16px; border: 2px solid {c['muted']}; border-radius: 9px;
    }}
    QWidget#settings_panel QRadioButton::indicator:checked {{ background: {c['focus']}; border-color: {c['focus']}; }}
    QWidget#settings_panel QPushButton[variant="theme-active"] {{
        background: {c['selected']}; color: {c['focus']}; border: 1px solid {c['focus']};
    }}
    QWidget#settings_panel QPushButton[variant="link"] {{
        background: transparent; color: {c['focus']}; border: 1px solid transparent;
    }}
    QWidget#settings_panel QPushButton[variant="link"]:hover {{ background: {c['hover']}; }}
    QWidget#settings_panel QPushButton[variant="link"]:focus {{ border-color: {c['focus']}; }}
    QWidget#settings_panel QLabel[role="account-status"] {{ color: {c['muted']}; font-size: 13px; }}
    QWidget#settings_panel QLabel[role="account-status"][state="saved"] {{ color: {c['success']}; }}
    QWidget#settings_panel QLabel[role="account-status"][state="error"] {{ color: {c['error']}; }}
    '''
