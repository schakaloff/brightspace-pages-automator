"""Scoped styles for the approved content workspace, in both app themes."""
import gui_styles


def colors():
    if gui_styles.is_light():
        return dict(bg='#fafbfd', text='#1c2b3b', muted='#536477', border='#dce4ec', field='#ffffff',
                    selected='#e0f0f0', accent='#005f63', button='#005f63', button_text='#ffffff')
    return dict(bg='#0d0d12', text='#dde0ee', muted='#a8b3c8', border='#292d39', field='#13131b',
                selected='#113338', accent='#82eee0', button='#82eee0', button_text='#102526')


def stylesheet():
    c = colors(); s = 'QWidget#duplicate_panel'
    tick = gui_styles.modern_asset('check-white.png')
    arrow = gui_styles.modern_asset('arrow-down-light.png' if gui_styles.is_light() else 'arrow-down-dark.png')
    return f'''
    {s}, {s} QWidget {{ background: {c['bg']}; color: {c['text']}; font-family: "Segoe UI"; font-size: 16px; }}
    {s} QLabel {{ border: none; background: transparent; }}
    {s} QLabel[role="heading"] {{ font-size: 32px; font-weight: 700; }}
    {s} QLabel[role="section"] {{ font-size: 24px; font-weight: 600; }}
    {s} QLabel[role="muted"] {{ color: {c['muted']}; font-size: 14px; }}
    {s} QLabel[role="field"] {{ font-weight: 600; font-size: 14px; }}
    {s} QFrame[role="line"] {{ background: {c['border']}; border: none; max-height: 1px; }}
    {s} QLineEdit, {s} QComboBox, {s} QSpinBox {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 9px 12px; min-height: 23px; }}
    {s} QLineEdit:focus, {s} QComboBox:focus, {s} QSpinBox:focus {{ border-color: {c['accent']}; }}
    {s} QComboBox::drop-down {{ border: none; width: 28px; background: transparent; }}
    {s} QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 8px; }}
    {s} QComboBox QAbstractItemView {{ background: {c['field']}; color: {c['text']}; selection-background-color: {c['selected']}; padding: 6px; border: 1px solid {c['border']}; }}
    {s} QScrollArea, {s} QTreeWidget {{ background: transparent; border: none; }}
    {s} QTreeWidget {{ outline: none; show-decoration-selected: 1; selection-color: {c['text']}; }}
    {s} QTreeWidget::item {{ padding: 10px 4px; border: none; min-height: 22px; }}
    {s} QTreeWidget::item:selected, {s} QTreeWidget::item:hover {{ background: {c['selected']}; color: {c['text']}; }}
    {s} QTreeWidget#copy_structure_preview {{ font-size: 14px; }}
    {s} QTreeWidget#copy_structure_preview::item {{ padding: 5px 2px; min-height: 18px; }}
    {s} QTreeWidget::indicator, {s} QCheckBox::indicator {{ width: 19px; height: 19px; border: 1px solid {c['muted']}; border-radius: 3px; background: {c['field']}; }}
    {s} QTreeWidget::indicator:checked, {s} QCheckBox::indicator:checked {{ background: #007a80; border-color: #007a80; image: url("{tick}"); }}
    {s} QCheckBox {{ spacing: 12px; padding: 4px 0; }}
    {s} QPushButton {{ color: {c['accent']}; background: transparent; border: none; padding: 10px 12px; min-height: 22px; border-radius: 6px; }}
    {s} QPushButton:hover {{ background: {c['selected']}; }}
    {s} QPushButton[variant="primary"] {{ background: {c['button']}; color: {c['button_text']}; font-weight: 700; padding: 12px 24px; }}
    {s} QPushButton[variant="primary"]:hover {{ background: {'#007a80' if gui_styles.is_light() else '#a4f5ea'}; }}
    {s} QPushButton:disabled {{ color: {c['muted']}; background: {c['field']}; }}
    {s} QPushButton[variant="stepper"] {{ border: 1px solid {c['border']}; background: {c['field']}; color: {c['text']}; min-width: 18px; font-size: 18px; }}
    {s} QSplitter::handle {{ background: {c['border']}; width: 1px; height: 1px; }}
    {s} QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
    {s} QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 28px; border-radius: 3px; }}
    {s} QScrollBar::add-page:vertical, {s} QScrollBar::sub-page:vertical {{ background: transparent; }}
    '''
