"""Scoped Check & fix styles using the existing native design tokens."""
import gui_styles


def checker_stylesheet():
    c = gui_styles.modern_colors()
    return f'''
    QWidget#checker_panel {{ background: {c['bg']}; }}
    QWidget#checker_panel QLabel {{ font-size: 15px; }}
    QWidget#checker_panel QLabel[role="dim"] {{ font-size: 14px; color: {c['muted']}; }}
    QWidget#checker_panel QLabel[role="page-heading"] {{ font-size: 28px; font-weight: 700; }}
    QWidget#checker_panel QLabel[role="section-heading"] {{ font-size: 22px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="field-label"] {{ font-size: 14px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="finding-title"] {{ font-size: 16px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="error"] {{ color: {c['error']}; font-size: 14px; }}
    QWidget#checker_panel QPushButton[role="finding-row"] {{
        background: transparent; color: {c['text']}; border: 1px solid transparent;
        border-bottom-color: {c['border']}; border-radius: 0; padding: 0;
    }}
    QWidget#checker_panel QPushButton[role="finding-row"]:hover {{ background: {c['hover']}; }}
    QWidget#checker_panel QPushButton[role="finding-row"]:focus {{ border-color: {c['focus']}; }}
    QWidget#checker_panel QPushButton {{ font-size: 14px; }}
    QWidget#checker_panel QPushButton[variant="link"] {{
        background: transparent; color: {c['focus']}; border: 1px solid transparent;
        padding: 8px 6px; font-weight: 400;
    }}
    QWidget#checker_panel QPushButton[variant="link"]:hover {{ background: {c['hover']}; }}
    QWidget#checker_panel QPushButton[variant="link"]:focus {{ border-color: {c['focus']}; }}
    '''
