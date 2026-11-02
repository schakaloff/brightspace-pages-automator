"""Render native Qt Build & style states without opening browsers or changing settings."""
import argparse
import os
from pathlib import Path
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
import gui_styles
from panels.workflow_hub import BuildStyleHub


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shell', action='store_true')
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle('Fusion')
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / name))
    app.setFont(QFont('Segoe UI', 10))
    output = ROOT / 'test-results' / 'build-style'
    output.mkdir(parents=True, exist_ok=True)
    if args.shell:
        from gui import MainWindow
        import update_checker
        import panels.h5p_panel
        update_checker.get_update_diagnostics = lambda: {}
        panels.h5p_panel.saved_runs = lambda *_: []
        for method in ('_set_window_icon', '_load_api_key', '_start_chromium_check',
                       '_start_update_check', '_show_welcome_if_needed', '_refresh_update_diagnostics'):
            setattr(MainWindow, method, lambda self: None)
        MainWindow.load_config = lambda self: dict(welcomed=True)
        MainWindow.save_config = lambda self, data: None
        MainWindow.closeEvent = lambda self, event: event.accept()
        window = MainWindow()
        hub = window._build_hub
        window._on_step(7)
    else:
        window = hub = BuildStyleHub()
    window.show()

    def capture(name, keep_focus=False):
        for _ in range(4):
            app.processEvents()
        if not keep_focus and app.focusWidget():
            app.focusWidget().clearFocus()
            app.processEvents()
        window.repaint()
        window.grab().save(str(output / name))

    for theme in ('light', 'dark'):
        if args.shell:
            window.set_theme(theme)
        else:
            gui_styles.set_theme(theme)
            app.setStyleSheet(gui_styles.get_stylesheet())
            hub.refresh_theme()
        for width, height in ((1120, 800), (624, 640), (560, 480)) if args.shell else ((1120, 685), (560, 640)):
            window.resize(width, height)
            if app.focusWidget():
                app.focusWidget().clearFocus()
            capture(f'{theme}-{width}-' + ('shell.png' if args.shell else 'panel.png'))
        window.resize(1120, 800 if args.shell else 685)
        row = hub.buttons['collect']
        row.setFocus()
        capture(f'{theme}-focus-' + ('shell.png' if args.shell else 'panel.png'), keep_focus=True)
        row.clearFocus()
        window.resize(560, 480)
        app.processEvents()
        hub._scroll.verticalScrollBar().setValue(hub._scroll.verticalScrollBar().maximum())
        capture(f'{theme}-bottom-' + ('shell.png' if args.shell else 'panel.png'))
        hub._scroll.verticalScrollBar().setValue(0)
    window.close()
    print(output)


if __name__ == '__main__':
    main()
