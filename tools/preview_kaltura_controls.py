"""Render and check the implemented Kaltura disclosure controls.

Uses existing widgets, bundled Tabler icons and fictitious data. No workers run.
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import gui_styles
from panels.kaltura_panel import KalturaResultsDialog
from preview_kaltura_workflow import SAMPLES

OUTPUT = ROOT / 'docs' / 'design' / 'kaltura-controls-2026-11-02' / 'implemented'


def make_dialog():
    dialog = KalturaResultsDialog()
    dialog.results.set_entries([
        dict(name=name, section_name=section, entry_id=id_, moodle_url='https://moodle.example')
        for name, section, id_ in SAMPLES
    ])
    dialog.results.set_modules([
        dict(id=str(i), title=name)
        for i, name in enumerate(('Welcome', 'Week 1 · Foundations', 'Week 2 · Practice'), 1)
    ])
    dialog.results.autosuggest()
    dialog.results.apply_default_selection()
    dialog.results.set_message('', False)
    dialog.set_summary('7 videos selected. Review destinations before creating pages.')
    return dialog


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle('Fusion')
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    app.setFont(QFont('Segoe UI', 10))
    def capture(dialog, name, size=(860, 680)):
        dialog.resize(*size)
        dialog.show()
        for _ in range(5):
            app.processEvents()
        assert dialog.grab().save(str(OUTPUT / name))
    for theme in ('dark', 'light'):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet())
        dialog = make_dialog()
        selections = dialog.results.selected_entries()
        destinations = dialog.results.section_map()
        capture(dialog, f'after-closed-{theme}.png')
        group = dialog.results._groups[0]
        group.expand_btn.click()
        assert group.expand_btn.isChecked() and not group.video_list_container.isHidden()
        assert group.expand_btn.text() == '' and not group.expand_btn.icon().isNull()
        assert group.expand_btn.accessibleName().startswith('Hide videos')
        capture(dialog, f'after-open-{theme}.png')
        capture(dialog, f'after-open-narrow-{theme}.png', (560, 620))
        # Refresh an already-open group across a live theme change.
        original_icon = group.expand_btn.icon().pixmap(18, 18).toImage()
        opposite = 'light' if theme == 'dark' else 'dark'
        gui_styles.set_theme(opposite)
        app.setStyleSheet(gui_styles.get_stylesheet())
        dialog.results.refresh_theme()
        assert group.expand_btn.isChecked()
        assert group.expand_btn.icon().pixmap(18, 18).toImage() != original_icon
        assert f'kaltura-chevron-down-{opposite}.png' in group.combo.styleSheet()
        group.expand_btn.setFocus()
        QTest.keyClick(group.expand_btn, Qt.Key.Key_Space)
        assert group.video_list_container.isHidden()
        assert group.expand_btn.accessibleName().startswith('Show videos')
        QTest.mouseClick(group.combo, Qt.MouseButton.LeftButton,
                         pos=QPoint(group.combo.width() - 16, group.combo.height() // 2))
        app.processEvents()
        assert group.combo.view().isVisible()
        group.combo.hidePopup()
        assert dialog.results.selected_entries() == selections
        assert dialog.results.section_map() == destinations
        dialog.close()
    from PIL import Image, ImageDraw, ImageFont
    sheet = Image.new('RGB', (1052, 880), '#e8edf2')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf'), 20)
    for index, (source, title) in enumerate((
        ('after-closed-dark.png', 'Implemented · compact chevrons'),
        ('after-open-dark.png', 'Implemented · group expanded'),
        ('after-open-light.png', 'Implemented · light theme'),
        ('after-open-narrow-light.png', 'Implemented · smaller window'),
    )):
        x, y = 14 + (index % 2) * 520, 12 + (index // 2) * 432
        draw.text((x, y), title, font=font, fill='#1c2b3b')
        shot = Image.open(OUTPUT / source).convert('RGB')
        shot.thumbnail((506, 394), Image.Resampling.LANCZOS)
        sheet.paste(shot, (x, y + 30))
    sheet.save(OUTPUT / 'comparison.png')
    print(f'Preview: {OUTPUT}')
    print('Verified mouse/keyboard expansion, dropdown opening, live theme changes, accessible labels, and unchanged selections/mappings.')


if __name__ == '__main__':
    main()
