"""Scoped Check & fix styles shared with the H5P/Media palette."""
import gui_styles


def checker_stylesheet():
    c = gui_styles.modern_colors()
    return f'''
    QWidget#checker_panel {{ background: {gui_styles.current['BG']}; }}
    QWidget#checker_panel QLabel {{ font-size: 15px; }}
    QWidget#checker_panel QLabel[role="dim"] {{ font-size: 14px; color: {c['muted']}; }}
    QWidget#checker_panel QLabel[role="hint"] {{ font-size: 13px; color: {c['muted']}; }}
    QWidget#checker_panel QLabel[role="page-heading"] {{ font-size: 28px; font-weight: 700; }}
    QWidget#checker_panel QLabel[role="section-heading"] {{ font-size: 18px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="field-label"] {{ font-size: 14px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="finding-title"] {{ font-size: 16px; font-weight: 600; }}
    QWidget#checker_panel QLabel[role="error"] {{ color: {c['error']}; font-size: 14px; }}
    QWidget#checker_panel QLabel[role="outcome-success"] {{ color: {c['success']}; }}
    QWidget#checker_panel QFrame[role="checker-surface"] {{
        background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
    }}
    QWidget#checker_panel QFrame[role="checker-step"] {{ border: none; border-radius: 10px; background: transparent; }}
    QWidget#checker_panel QFrame[role="checker-step"][state="active"] {{ background: {c['selected']}; }}
    QWidget#checker_panel QFrame[role="checker-step"] QLabel {{ color: {c['muted']}; font-size: 14px; }}
    QWidget#checker_panel QFrame[role="checker-step"][state="active"] QLabel {{ color: {c['focus']}; }}
    QWidget#checker_panel QFrame[role="checker-step"][state="complete"] QLabel {{ color: {c['success']}; }}
    QWidget#checker_panel QFrame[role="checker-icon-tile"] {{ background: {c['soft']}; border: none; border-radius: 8px; }}
    QWidget#checker_panel QFrame[role="checker-fix-row"] {{ border: none; border-top: 1px solid {c['border']}; }}
    QWidget#checker_panel QPushButton {{ font-size: 14px; }}
    QWidget#checker_panel QPushButton[variant="link"] {{
        background: transparent; color: {c['focus']}; border: 1px solid transparent;
        padding: 4px 6px; font-weight: 400;
    }}
    QWidget#checker_panel QPushButton[variant="link"]:hover {{ background: {c['hover']}; }}
    QWidget#checker_panel QPushButton[variant="link"]:focus {{ border-color: {c['focus']}; }}
    QWidget#checker_panel QCheckBox:focus {{ outline: 1px solid {c['focus']}; }}
    QWidget#checker_panel QProgressBar {{ background: {c['soft']}; border: none; border-radius: 2px; }}
    QWidget#checker_panel QProgressBar::chunk {{ background: {c['focus']}; border-radius: 2px; }}
    '''
