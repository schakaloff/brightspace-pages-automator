"""Offline packaged-app smoke mode. No login, course requests or live writes."""
import json
import sys
from pathlib import Path


def main():
    from PySide6.QtWidgets import QApplication, QMainWindow
    from PySide6.QtCore import QTimer
    import panels.transfer_panel as panel_module
    from transfer_studio import TransferStudio

    output = Path(sys.argv[sys.argv.index("--studio-smoke") + 1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    panel_module.DATA_DIR = output / "plans"
    app = QApplication.instance() or QApplication(sys.argv)
    window = QMainWindow()
    window.load_config = lambda: {}
    window.save_config = lambda data: None
    window.chromium_ready = True
    panel = panel_module.TransferPanel(window)
    source = {"id": "1", "name": "Offline source", "root": "/content/enforced/1-src/",
              "modules": [{"Id": 10, "Type": 0, "Title": "Example module", "children": [
                  {"Id": 11, "Type": 1, "Title": "Example page", "ActivityType": 1, "TopicType": 1,
                   "Url": "/content/enforced/1-src/page.html", "html": "<h1>Example page</h1><p>Offline bundle check.</p>"}]}],
              "assignments": [], "categories": [], "grades": [], "setup": {}, "versions": []}
    target = {**source, "id": "2", "name": "Offline destination", "root": "/content/enforced/2-tgt/", "modules": []}
    panel._studio = TransferStudio(panel_module.DATA_DIR, lambda request: panel._studio.finish_scan((source, target)))
    url = panel._studio.start()
    # An ephemeral launch capability, consumed and removed by the smoke checker.
    (output / "ready.json").write_text(json.dumps({"url": url}), encoding="utf-8")
    def close():
        panel._timer.stop(); panel.close_studio(); app.quit()
    timer = QTimer()
    timer.timeout.connect(lambda: close() if (output / "stop").exists() else None)
    timer.start(100)
    QTimer.singleShot(60000, close)
    return app.exec()
