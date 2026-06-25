# PySide6 GUI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `gui.py` from CustomTkinter to PySide6 with a sidebar step-rail layout, QPainter icons, zoomable log widget, and a shared Settings panel for the Gemini API key — all on the `feature/gui-redesign` branch.

**Architecture:** Sidebar (`Sidebar` + `StepButton`) sits left; a `QStackedWidget` holds four panels (Checker, Collector, Restyle, Settings). Worker threads communicate via `queue.Queue`, polled every 100 ms by `QTimer`. All backend `src/*.py` files are untouched.

**Tech Stack:** PySide6 ≥ 6.7, Python 3.10+, Pillow (existing), pytest + pytest-qt (tests only)

## Global Constraints

- Branch: `feature/gui-redesign` — never commit to `main` or `dev`
- `src/automator.py`, `src/browser.py`, `src/content_checker.py`, `src/unit_collector.py`, `src/ai_styler.py`, `src/chromium_setup.py`, `src/icon_art.py`, `src/update_checker.py` — **read-only**, never modified
- `user_config.json` format unchanged
- All emoji removed from UI; replaced by QPainter icons or plain text
- `App.gemini_api_key` is the single source of truth — panels never have their own key field
- `__SUCCESS__` queue message (not `__DONE__`) triggers step unlock
- PySide6 `QTimer` replaces every `self.after()` call

---

### Task 1: Branch + Dependencies

**Files:**
- Modify: `brightspace-pages-automator/requirements.txt` (create if absent)
- Modify: `brightspace-pages-automator/installer/brightspace_automator.spec`

- [ ] **Step 1: Create the feature branch**

```bash
cd "brightspace-pages-automator"
git checkout -b feature/gui-redesign
```

Expected: `Switched to a new branch 'feature/gui-redesign'`

- [ ] **Step 2: Install PySide6 and pytest-qt into the venv**

```bash
pip install "PySide6>=6.7" pytest-qt
```

- [ ] **Step 3: Verify PySide6 works**

```bash
python -c "from PySide6.QtWidgets import QApplication; print('PySide6 OK')"
```

Expected output: `PySide6 OK`

- [ ] **Step 4: Create/update requirements.txt**

Create `requirements.txt` with these contents (add any existing entries already present):

```
PySide6>=6.7
playwright
pillow
google-generativeai
python-dotenv
requests
pytest
pytest-qt
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "feat: add PySide6 dependency for gui redesign"
```

---

### Task 2: Color Constants and QSS (`src/gui_styles.py`)

**Files:**
- Create: `src/gui_styles.py`
- Create: `tests/test_gui_styles.py`

**Interfaces:**
- Produces: `BG, SIDEBAR, PANEL, BORDER, BORDER_ACT, TEXT_PRI, TEXT_SEC, TEXT_FAINT, OC_TEAL, OC_TEAL_MID, OC_TEAL_BG, OC_ORANGE, DONE, RUNNING, LOCKED_BG, ERROR, LOG_INFO, LOG_SUCCESS, LOG_ERROR, LOG_WARNING, LOG_STEP, LOG_DIM, APP_STYLESHEET` — all module-level strings

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_styles.py
import sys
sys.path.insert(0, "src")
from gui_styles import APP_STYLESHEET, BG, OC_ORANGE, OC_TEAL

def test_colors_are_hex():
    for val in (BG, OC_ORANGE, OC_TEAL):
        assert val.startswith("#"), f"expected hex, got {val!r}"
        assert len(val) == 7

def test_stylesheet_is_nonempty_string():
    assert isinstance(APP_STYLESHEET, str)
    assert len(APP_STYLESHEET) > 200
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_gui_styles.py -v
```

Expected: `ImportError` — module not found.

- [ ] **Step 3: Create `src/gui_styles.py`**

```python
# src/gui_styles.py

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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gui_styles.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gui_styles.py tests/test_gui_styles.py
git commit -m "feat: add color constants and QSS stylesheet"
```

---

### Task 3: QPainter Icon Library (`src/gui_icons.py`)

**Files:**
- Create: `src/gui_icons.py`
- Create: `tests/test_gui_icons.py`

**Interfaces:**
- Produces: `make_icon(name, color, size=16) -> QIcon`, `make_pixmap(name, color, size=16) -> QPixmap`
- Valid names: `"checker"`, `"collect"`, `"restyle"`, `"settings"`, `"run"`, `"next"`, `"done"`, `"locked"`, `"running"`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_icons.py
import sys
sys.path.insert(0, "src")
import pytest

@pytest.fixture(scope="session")
def qapp(qapp):
    return qapp

ALL_ICONS = ["checker", "collect", "restyle", "settings", "run", "next", "done", "locked", "running"]

def test_all_icons_return_qicon(qapp):
    from gui_icons import make_icon
    from PySide6.QtGui import QIcon
    for name in ALL_ICONS:
        icon = make_icon(name, "#ffffff")
        assert isinstance(icon, QIcon), f"make_icon({name!r}) did not return QIcon"
        assert not icon.isNull(), f"make_icon({name!r}) returned null QIcon"

def test_make_pixmap_returns_correct_size(qapp):
    from gui_icons import make_pixmap
    px = make_pixmap("run", "#ff0000", size=24)
    assert px.width() == 24
    assert px.height() == 24

def test_unknown_icon_raises(qapp):
    from gui_icons import make_icon
    with pytest.raises(KeyError):
        make_icon("nonexistent", "#fff")
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
pytest tests/test_gui_icons.py -v
```

Expected: ImportError.

- [ ] **Step 3: Create `src/gui_icons.py`**

```python
# src/gui_icons.py
import math
from PySide6.QtGui import (
    QPainter, QPixmap, QIcon, QPen, QBrush,
    QPainterPath, QColor, QPolygonF,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QRect


def _make_pixmap(size: int, draw_fn, color: str) -> QPixmap:
    scale = 2
    px = QPixmap(size * scale, size * scale)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    draw_fn(p, size, QColor(color))
    p.end()
    return px.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def _checker(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    r = s * 0.28
    cx = s / 2
    cy = s / 2
    p.drawEllipse(QRectF(cx - r - 2, cy - r, r * 2, r * 2))
    p.drawEllipse(QRectF(cx - r + 2, cy - r, r * 2, r * 2))


def _collect(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    w = s * 0.65; h = s * 0.14
    for i, yo in enumerate([0, s * 0.23, s * 0.46]):
        x = (s - w) / 2 + i * 1.5
        y = s * 0.18 + yo
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w - i * 1.5, h), 2, 2)
        p.drawPath(path)


def _restyle(p, s, c):
    pen = QPen(c, 2.2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.2, s * 0.8), QPointF(s * 0.72, s * 0.28))
    sq = s * 0.13
    p.setBrush(QBrush(c)); p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(s * 0.72 - sq / 2, s * 0.28 - sq / 2, sq, sq))


def _settings(p, s, c):
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(c))
    cx, cy = s / 2, s / 2
    outer, inner = s * 0.38, s * 0.17
    pts = []
    for i in range(16):
        angle = math.radians(i * 22.5 - 90)
        r = outer if i % 2 == 0 else inner
        pts.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    p.drawPolygon(QPolygonF(pts))


def _run(p, s, c):
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(c))
    m = s * 0.22
    p.drawPolygon(QPolygonF([QPointF(m, m), QPointF(m, s - m), QPointF(s - m, s / 2)]))


def _next_arrow(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    cy = s / 2
    p.drawLine(QPointF(s * 0.12, cy), QPointF(s * 0.78, cy))
    p.drawLine(QPointF(s * 0.62, s * 0.32), QPointF(s * 0.86, cy))
    p.drawLine(QPointF(s * 0.62, s * 0.68), QPointF(s * 0.86, cy))


def _done(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.08
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
    path = QPainterPath()
    path.moveTo(QPointF(s * 0.28, s * 0.52))
    path.lineTo(QPointF(s * 0.44, s * 0.68))
    path.lineTo(QPointF(s * 0.72, s * 0.36))
    p.drawPath(path)


def _locked(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    bx, by = s * 0.26, s * 0.50
    bw, bh = s * 0.48, s * 0.38
    path = QPainterPath(); path.addRoundedRect(QRectF(bx, by, bw, bh), 3, 3)
    p.drawPath(path)
    p.drawArc(QRectF(s * 0.32, s * 0.16, s * 0.36, s * 0.46), 0, 180 * 16)


def _running(p, s, c):
    pen = QPen(c, 2.2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.1
    p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 90 * 16, -270 * 16)


_FNS = {
    "checker": _checker, "collect": _collect, "restyle": _restyle,
    "settings": _settings, "run": _run, "next": _next_arrow,
    "done": _done, "locked": _locked, "running": _running,
}


def make_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    return _make_pixmap(size, _FNS[name], color)


def make_icon(name: str, color: str, size: int = 16) -> QIcon:
    return QIcon(make_pixmap(name, color, size))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gui_icons.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gui_icons.py tests/test_gui_icons.py
git commit -m "feat: add QPainter icon library"
```

---

### Task 4: Log Widget (`src/gui_log.py`)

**Files:**
- Create: `src/gui_log.py`
- Create: `tests/test_gui_log.py`

**Interfaces:**
- Produces: `LogWidget(parent=None)` — QTextEdit subclass
- Methods: `append_log(text: str, tag: str = "info")`, `clear_log()`
- Tags: `"info"`, `"success"`, `"error"`, `"warning"`, `"step"`, `"dim"`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_log.py
import sys
sys.path.insert(0, "src")
import pytest

def test_append_adds_text(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("hello world", "info")
    assert "hello world" in w.toPlainText()

def test_clear_removes_text(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("some text", "info")
    w.clear_log()
    assert w.toPlainText().strip() == ""

def test_unknown_tag_does_not_raise(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("msg", "unknown_tag")  # should not raise

def test_zoom_badge_hidden_initially(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    assert not w._zoom_badge.isVisible()
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_gui_log.py -v
```

Expected: ImportError.

- [ ] **Step 3: Create `src/gui_log.py`**

```python
# src/gui_log.py
from PySide6.QtWidgets import QTextEdit, QLabel
from PySide6.QtGui import QTextCharFormat, QColor, QFont, QFontDatabase, QKeySequence
from PySide6.QtCore import Qt, QTimer

from gui_styles import (
    LOG_INFO, LOG_SUCCESS, LOG_ERROR, LOG_WARNING, LOG_STEP, LOG_DIM,
    TEXT_SEC, BG,
)

_TAG_COLORS = {
    "info":    LOG_INFO,
    "success": LOG_SUCCESS,
    "error":   LOG_ERROR,
    "warning": LOG_WARNING,
    "step":    LOG_STEP,
    "dim":     LOG_DIM,
}
_LOG_FONTS = ["Cascadia Code", "JetBrains Mono", "Consolas", "Courier New"]


class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._setup_font()
        self._at_bottom = True
        self.verticalScrollBar().valueChanged.connect(self._track_scroll)
        self._setup_zoom_badge()

    def _setup_font(self):
        available = QFontDatabase.families()
        family = next((f for f in _LOG_FONTS if f in available), "monospace")
        self.setFont(QFont(family, 13))

    def _setup_zoom_badge(self):
        self._zoom_level = 100
        self._zoom_badge = QLabel("100%", self)
        self._zoom_badge.setStyleSheet(
            f"color:{TEXT_SEC};background:rgba(13,13,18,200);"
            "padding:2px 6px;border-radius:3px;font-size:10px;"
        )
        self._zoom_badge.hide()
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._zoom_badge.hide)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_badge()

    def _reposition_badge(self):
        self._zoom_badge.adjustSize()
        self._zoom_badge.move(
            self.width() - self._zoom_badge.width() - 10,
            self.height() - self._zoom_badge.height() - 10,
        )

    def _show_zoom_badge(self):
        self._zoom_badge.setText(f"{self._zoom_level}%")
        self._reposition_badge()
        self._zoom_badge.show()
        self._zoom_badge.raise_()
        self._zoom_timer.start(1500)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoomIn(1)
                self._zoom_level = min(200, self._zoom_level + 10)
            else:
                self.zoomOut(1)
                self._zoom_level = max(50, self._zoom_level - 10)
            self._show_zoom_badge()
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.ZoomIn):
            self.zoomIn(1); self._zoom_level = min(200, self._zoom_level + 10)
            self._show_zoom_badge()
        elif event.matches(QKeySequence.StandardKey.ZoomOut):
            self.zoomOut(1); self._zoom_level = max(50, self._zoom_level - 10)
            self._show_zoom_badge()
        else:
            super().keyPressEvent(event)

    def _track_scroll(self, value):
        self._at_bottom = value >= self.verticalScrollBar().maximum() - 5

    def append_log(self, text: str, tag: str = "info"):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_TAG_COLORS.get(tag, LOG_INFO)))
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        if self._at_bottom:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def clear_log(self):
        self.clear()
        self._at_bottom = True
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gui_log.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gui_log.py tests/test_gui_log.py
git commit -m "feat: add LogWidget with Ctrl+scroll zoom and auto-scroll"
```

---

### Task 5: Sidebar (`src/gui_sidebar.py`)

**Files:**
- Create: `src/gui_sidebar.py`
- Create: `tests/test_gui_sidebar.py`

**Interfaces:**
- Produces: `StepButton(number, icon_name, label)` — QPushButton subclass with `.set_state(state)`, `.get_state()`
- Produces: `Sidebar(steps)` — QWidget with signals `step_clicked(int)`, `settings_clicked()`; method `set_step_state(n, state)`, `set_active(n)`
- States: `StepButton.LOCKED`, `PENDING`, `ACTIVE`, `DONE`, `RUNNING`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_sidebar.py
import sys
sys.path.insert(0, "src")

def test_step_button_initial_state_locked(qtbot):
    from gui_sidebar import StepButton
    btn = StepButton(1, "checker", "Checker")
    qtbot.addWidget(btn)
    assert btn.get_state() == StepButton.LOCKED
    assert not btn.isEnabled()

def test_step_button_unlock(qtbot):
    from gui_sidebar import StepButton
    btn = StepButton(1, "checker", "Checker")
    qtbot.addWidget(btn)
    btn.set_state(StepButton.PENDING)
    assert btn.get_state() == StepButton.PENDING
    assert btn.isEnabled()

def test_sidebar_step_clicked_signal(qtbot):
    from gui_sidebar import Sidebar
    sidebar = Sidebar([(1, "checker", "Checker"), (2, "collect", "Collect")])
    qtbot.addWidget(sidebar)
    received = []
    sidebar.step_clicked.connect(received.append)
    # Step 1 starts locked — unlock it first
    sidebar.set_step_state(1, "pending")
    sidebar._step_buttons[1].click()
    assert received == [1]

def test_sidebar_set_active_marks_active(qtbot):
    from gui_sidebar import Sidebar, StepButton
    sidebar = Sidebar([(1, "checker", "Checker")])
    qtbot.addWidget(sidebar)
    sidebar.set_step_state(1, StepButton.PENDING)
    sidebar.set_active(1)
    assert sidebar._step_buttons[1].get_state() == StepButton.ACTIVE
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_gui_sidebar.py -v
```

Expected: ImportError.

- [ ] **Step 3: Create `src/gui_sidebar.py`**

```python
# src/gui_sidebar.py
import math
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QPolygonF
from PySide6.QtCore import Qt, Signal, QRect, QRectF, QPointF, QSize

from gui_styles import (
    OC_ORANGE, OC_TEAL, TEXT_PRI, TEXT_SEC, TEXT_FAINT,
    PANEL, DONE, RUNNING, BORDER, SIDEBAR,
)
from gui_icons import make_pixmap


class StepButton(QPushButton):
    LOCKED  = "locked"
    PENDING = "pending"
    ACTIVE  = "active"
    DONE    = "done"
    RUNNING = "running"

    def __init__(self, number: int, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._number    = number
        self._icon_name = icon_name
        self._label     = label
        self._state     = self.LOCKED
        self.setFixedHeight(52)
        self.setProperty("class", "step-btn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_state()

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        self._apply_state()
        self.update()

    def get_state(self) -> str:
        return self._state

    def _apply_state(self):
        self.setEnabled(self._state != self.LOCKED)
        self.setProperty("active", self._state == self.ACTIVE)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        locked = self._state == self.LOCKED

        # Step number chip
        chip_w, chip_h = 20, 14
        chip_x = 12
        chip_y = (h - chip_h) // 2
        chip_color = OC_ORANGE if not locked else TEXT_FAINT
        p.setBrush(QBrush(QColor(chip_color + "22")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(chip_x, chip_y, chip_w, chip_h, 3, 3)
        p.setPen(QPen(QColor(chip_color)))
        nf = QFont(); nf.setPointSize(7); nf.setBold(True)
        p.setFont(nf)
        p.drawText(QRect(chip_x, chip_y, chip_w, chip_h),
                   Qt.AlignmentFlag.AlignCenter, str(self._number))

        # Icon
        icon_x = chip_x + chip_w + 8
        icon_y = (h - 16) // 2
        icon_color = TEXT_PRI if self._state == self.ACTIVE else TEXT_SEC
        px = make_pixmap(self._icon_name, icon_color, 16)
        p.setOpacity(0.4 if locked else 1.0)
        p.drawPixmap(icon_x, icon_y, px)
        p.setOpacity(1.0)

        # Label
        label_x = icon_x + 16 + 8
        label_color = TEXT_PRI if self._state == self.ACTIVE else (
            TEXT_FAINT if locked else TEXT_SEC
        )
        lf = QFont(); lf.setPointSize(9); lf.setBold(True)
        p.setPen(QPen(QColor(label_color)))
        p.setFont(lf)
        p.setOpacity(0.4 if locked else 1.0)
        p.drawText(QRect(label_x, 0, w - label_x - 28, h),
                   Qt.AlignmentFlag.AlignVCenter, self._label)
        p.setOpacity(1.0)

        # Status indicator
        dot_x = w - 16
        dot_y = h // 2
        dot_r = 4
        if self._state == self.DONE:
            p.setBrush(QBrush(QColor(DONE))); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.RUNNING:
            p.setBrush(QBrush(QColor(RUNNING))); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.LOCKED:
            p.setOpacity(0.35)
            lk = make_pixmap("locked", TEXT_SEC, 12)
            p.drawPixmap(dot_x - 6, dot_y - 6, lk)
            p.setOpacity(1.0)
        elif self._state == self.PENDING:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(TEXT_FAINT), 1.2))
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)

        p.end()


class Sidebar(QWidget):
    step_clicked     = Signal(int)
    settings_clicked = Signal()

    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(160)
        self._step_buttons: dict[int, StepButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # App header
        header = QWidget(); header.setFixedHeight(64)
        hl = QHBoxLayout(header); hl.setContentsMargins(14, 10, 14, 10)
        info = QWidget(); iv = QVBoxLayout(info)
        iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(1)
        top = QLabel("Brightspace")
        top.setStyleSheet(f"color:{TEXT_FAINT};font-size:9px;font-weight:700;")
        bot = QLabel("Automator")
        bot.setStyleSheet(f"color:{TEXT_PRI};font-size:13px;font-weight:700;")
        iv.addWidget(top); iv.addWidget(bot)
        hl.addWidget(info)
        layout.addWidget(header)

        # Divider
        div = QWidget(); div.setFixedHeight(1)
        div.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(div)
        layout.addSpacing(6)

        # Step buttons
        for number, icon_name, label in steps:
            btn = StepButton(number, icon_name, label)
            btn.clicked.connect(lambda _=False, n=number: self.step_clicked.emit(n))
            self._step_buttons[number] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # Bottom divider
        div2 = QWidget(); div2.setFixedHeight(1)
        div2.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(div2)

        # Settings button
        self._settings_btn = QPushButton("  Settings")
        self._settings_btn.setProperty("class", "step-btn")
        self._settings_btn.setFixedHeight(44)
        self._settings_btn.setIcon(make_pixmap("settings", TEXT_SEC, 16))  # type: ignore
        self._settings_btn.clicked.connect(self.settings_clicked)
        layout.addWidget(self._settings_btn)
        layout.addSpacing(4)

    def set_step_state(self, number: int, state: str):
        if number in self._step_buttons:
            self._step_buttons[number].set_state(state)

    def set_active(self, number: int | None):
        for n, btn in self._step_buttons.items():
            if btn.get_state() not in (StepButton.LOCKED, StepButton.DONE, StepButton.RUNNING):
                btn.set_state(StepButton.PENDING)
        if number is not None and number in self._step_buttons:
            if self._step_buttons[number].get_state() != StepButton.LOCKED:
                self._step_buttons[number].set_state(StepButton.ACTIVE)
        is_settings = number is None
        self._settings_btn.setProperty("active", is_settings)
        self._settings_btn.style().unpolish(self._settings_btn)
        self._settings_btn.style().polish(self._settings_btn)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gui_sidebar.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gui_sidebar.py tests/test_gui_sidebar.py
git commit -m "feat: add StepButton and Sidebar widgets"
```

---

### Task 6: MainWindow Shell (`gui.py`)

**Files:**
- Create: `gui.py` (replaces existing — keep old one as `gui_ctk_backup.py`)

**Interfaces:**
- Produces: `MainWindow` — `QMainWindow` with sidebar + stacked panels
- Produces: `MainWindow.gemini_api_key: str` property
- Produces: `MainWindow.chromium_ready: bool` property

- [ ] **Step 1: Back up the existing gui.py**

```bash
cp gui.py gui_ctk_backup.py
```

- [ ] **Step 2: Create the new `gui.py`**

```python
# gui.py
import json
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QStackedWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap

from gui_styles import APP_STYLESHEET, BG
from gui_sidebar import Sidebar, StepButton
from gui_icons import make_icon

VERSION = "0.8.0"
_CONFIG_PATH = Path(__file__).parent / "user_config.json"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Brightspace Pages Automator")
        self.setMinimumSize(960, 760)
        self.resize(1100, 800)
        self._gemini_key   = ""
        self._chromium_ready = False
        self._set_window_icon()
        self._build_ui()
        self._load_api_key()
        self._start_chromium_check()
        self._start_update_check()

    # ── Window icon (PIL → QPixmap) ──────────────────────────
    def _set_window_icon(self):
        try:
            from icon_art import draw_app_icon
            from PIL.ImageQt import ImageQt
            img = draw_app_icon(64)
            self.setWindowIcon(QIcon(QPixmap.fromImage(ImageQt(img))))
        except Exception:
            pass

    # ── UI ───────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = Sidebar([
            (1, "checker", "Checker"),
            (2, "collect", "Collect"),
            (3, "restyle", "Restyle"),
        ])
        self._sidebar.step_clicked.connect(self._on_step)
        self._sidebar.settings_clicked.connect(self._on_settings)
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("content")
        root.addWidget(self._stack, 1)

        # Panels imported lazily to keep imports fast
        from gui_panels import CheckerPanel, CollectorPanel, RestylePanel, SettingsPanel

        self._checker   = CheckerPanel(self)
        self._collector = CollectorPanel(self)
        self._restyle   = RestylePanel(self)
        self._settings  = SettingsPanel(self)

        for panel in (self._checker, self._collector, self._restyle, self._settings):
            self._stack.addWidget(panel)  # indices 0-3

        # Cross-panel wiring
        self._checker.step_success.connect(lambda: self._unlock_step(2))
        self._checker.continue_next.connect(lambda: self._on_step(2))
        self._collector.step_success.connect(lambda: self._unlock_step(3))
        self._collector.continue_next.connect(lambda: self._on_step(3))
        self._settings.api_key_changed.connect(self._set_api_key)

        self._on_step(1)

    def _on_step(self, n: int):
        idx = {1: 0, 2: 1, 3: 2}.get(n)
        if idx is not None:
            self._stack.setCurrentIndex(idx)
            self._sidebar.set_active(n)

    def _on_settings(self):
        self._stack.setCurrentIndex(3)
        self._sidebar.set_active(None)

    def _unlock_step(self, n: int):
        self._sidebar.set_step_state(n, StepButton.PENDING)

    # ── Gemini API key ───────────────────────────────────────
    @property
    def gemini_api_key(self) -> str:
        return self._gemini_key

    def _set_api_key(self, key: str):
        self._gemini_key = key

    def _load_api_key(self):
        key = ""
        try:
            from api_config import GEMINI_API_KEY as k
            key = k
        except ImportError:
            pass
        if not key:
            try:
                key = json.loads(_CONFIG_PATH.read_text())\.get("gemini_api_key", "")
            except Exception:
                pass
        self._gemini_key = key
        self._settings.set_api_key(key)

    # ── Config helpers ───────────────────────────────────────
    def load_config(self) -> dict:
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_config(self, data: dict):
        try:
            existing = self.load_config()
            existing.update(data)
            _CONFIG_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[config] save failed: {e}", flush=True)

    # ── Chromium check ───────────────────────────────────────
    @property
    def chromium_ready(self) -> bool:
        return self._chromium_ready

    def _start_chromium_check(self):
        self._chromium_q = queue.Queue()
        self._chromium_timer = QTimer(self)
        self._chromium_timer.timeout.connect(self._chromium_poll)
        self._chromium_timer.start(150)
        threading.Thread(target=self._chromium_worker, daemon=True).start()

    def _chromium_worker(self):
        from chromium_setup import is_chromium_installed, install_chromium
        if is_chromium_installed():
            self._chromium_q.put(("ready", None))
            return
        self._chromium_q.put(("need_install", None))
        ok, err = install_chromium(
            progress_cb=lambda line: self._chromium_q.put(("progress", line))
        )
        self._chromium_q.put(("done", (ok, err)))

    def _chromium_poll(self):
        try:
            while True:
                kind, payload = self._chromium_q.get_nowait()
                if kind == "ready":
                    self._chromium_ready = True
                elif kind == "need_install":
                    self._show_chromium_dialog()
                elif kind == "progress":
                    if hasattr(self, "_chromium_log"):
                        self._chromium_log.append_log(payload, "info")
                elif kind == "done":
                    ok, err = payload
                    if hasattr(self, "_chromium_dlg"):
                        self._chromium_dlg.accept()
                    if ok:
                        self._chromium_ready = True
                    else:
                        from PySide6.QtWidgets import QMessageBox
                        QMessageBox.critical(self, "Chromium setup failed",
                            f"Could not download the browser engine:\n{err}\n\n"
                            "Check your internet connection and restart.")
        except queue.Empty:
            pass

    def _show_chromium_dialog(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        from gui_log import LogWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Setting up browser engine")
        dlg.setFixedSize(480, 300)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Downloading browser engine (one-time setup)…"))
        log = LogWidget()
        layout.addWidget(log)
        self._chromium_dlg = dlg
        self._chromium_log = log
        dlg.show()

    # ── Update check ─────────────────────────────────────────
    def _start_update_check(self):
        self._update_q = queue.Queue()
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_poll)
        self._update_timer.start(2000)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        from update_checker import check_for_update
        release = check_for_update()
        if not release:
            return
        if self.load_config().get("skipped_update_tag") == release["tag"]:
            return
        self._update_q.put(release)

    def _update_poll(self):
        try:
            release = self._update_q.get_nowait()
            self._show_update_dialog(release)
            self._update_timer.stop()
        except queue.Empty:
            pass

    def _show_update_dialog(self, release: dict):
        from gui_dialogs import UpdateDialog
        dlg = UpdateDialog(release, self)
        dlg.exec()

    def closeEvent(self, event):
        self.save_config({
            "gemini_api_key": self._gemini_key,
        })
        super().closeEvent(event)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
```

**Note:** There is a syntax error intentionally left — `json.loads(_CONFIG_PATH.read_text())\.get(...)` — fix the backslash: `json.loads(_CONFIG_PATH.read_text()).get(...)`.

- [ ] **Step 3: Fix the typo in `_load_api_key`**

Line reads:
```python
key = json.loads(_CONFIG_PATH.read_text())\.get("gemini_api_key", "")
```
Should be:
```python
key = json.loads(_CONFIG_PATH.read_text()).get("gemini_api_key", "")
```

- [ ] **Step 4: Visual test — launch the shell**

```bash
python gui.py
```

Expected: Window appears, sidebar shows 3 steps (step 1 active, 2 and 3 locked), clicking steps 2/3 does nothing (locked), no crash.

- [ ] **Step 5: Commit**

```bash
git add gui.py gui_ctk_backup.py
git commit -m "feat: add PySide6 MainWindow shell with sidebar navigation"
```

---

### Task 7: Settings Panel (`src/gui_panels.py` — SettingsPanel only)

**Files:**
- Create: `src/gui_panels.py` (SettingsPanel only for now — other panels added in Tasks 8-10)
- Create: `tests/test_gui_panels.py`

**Interfaces:**
- Produces: `SettingsPanel(main_window)` — QWidget
- Signal: `api_key_changed(str)`
- Method: `set_api_key(key: str)`

- [ ] **Step 1: Write failing test**

```python
# tests/test_gui_panels.py
import sys
sys.path.insert(0, "src")

def test_settings_api_key_signal(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from gui_panels import SettingsPanel
    mw = QMainWindow()
    panel = SettingsPanel(mw)
    qtbot.addWidget(panel)
    received = []
    panel.api_key_changed.connect(received.append)
    panel.set_api_key("test-key-123")
    assert panel._key_field.text() == "test-key-123"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_gui_panels.py::test_settings_api_key_signal -v
```

Expected: ImportError.

- [ ] **Step 3: Create `src/gui_panels.py` with SettingsPanel**

```python
# src/gui_panels.py
import json
import os
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon

from gui_styles import (
    BG, PANEL, BORDER, TEXT_PRI, TEXT_SEC, TEXT_FAINT,
    OC_TEAL, OC_ORANGE,
)
from gui_log import LogWidget
from gui_icons import make_icon


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color:{BORDER};background:{BORDER};max-height:1px;")
    return line


def _form_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "form-label")
    return lbl


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "header")
    return lbl


class SettingsPanel(QWidget):
    api_key_changed = Signal(str)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_api_key)
        self._build()

    def _build(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header ──
        layout.addWidget(_section_header("Settings"))
        sub = QLabel("Shared configuration for all tabs.")
        sub.setProperty("role", "dim")
        layout.addWidget(sub)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Gemini API Key ──
        layout.addWidget(_form_label("GEMINI API KEY"))
        layout.addSpacing(6)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self._key_field = QLineEdit()
        self._key_field.setPlaceholderText("AIza…")
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setFixedHeight(40)
        self._key_field.textChanged.connect(self._on_key_changed)
        key_row.addWidget(self._key_field)

        show_btn = QPushButton("Show")
        show_btn.setProperty("variant", "secondary")
        show_btn.setFixedSize(60, 40)
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda on: self._key_field.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        show_btn.toggled.connect(lambda on: show_btn.setText("Hide" if on else "Show"))
        key_row.addWidget(show_btn)
        layout.addLayout(key_row)

        hint = QLabel("Used by Collect and Restyle tabs. Saved automatically.")
        hint.setProperty("role", "dim")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Downloads folder ──
        layout.addWidget(_form_label("DOWNLOADS FOLDER"))
        layout.addSpacing(6)

        dl_row = QHBoxLayout()
        downloads_path = Path(__file__).parent.parent / "downloads"
        path_lbl = QLabel(str(downloads_path))
        path_lbl.setProperty("role", "dim")
        path_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 11px;")
        dl_row.addWidget(path_lbl, 1)

        open_btn = QPushButton("Open Folder")
        open_btn.setProperty("variant", "secondary")
        open_btn.setFixedHeight(36)
        open_btn.clicked.connect(
            lambda: os.startfile(str(downloads_path) if downloads_path.exists()
                                  else str(downloads_path.parent))
        )
        dl_row.addWidget(open_btn)
        layout.addLayout(dl_row)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Guide ──
        layout.addWidget(_form_label("WORKFLOW GUIDE"))
        layout.addSpacing(6)
        guide_btn = QPushButton("Open Full Visual Guide in Browser")
        guide_btn.setFixedHeight(42)
        guide_path = Path(__file__).parent.parent / "WORKFLOW_GUIDE.html"
        guide_btn.clicked.connect(
            lambda: webbrowser.open(f"file:///{str(guide_path).replace(os.sep, '/')}")
        )
        layout.addWidget(guide_btn)
        layout.addSpacing(8)
        guide_hint = QLabel("Detailed step-by-step flowchart — shareable and printable.")
        guide_hint.setProperty("role", "dim")
        layout.addWidget(guide_hint)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Version ──
        from gui_styles import TEXT_FAINT
        ver = QLabel(f"Brightspace Pages Automator  v0.8.0")
        ver.setStyleSheet(f"color:{TEXT_FAINT}; font-size:11px;")
        layout.addWidget(ver)
        layout.addStretch()

    def set_api_key(self, key: str):
        self._key_field.blockSignals(True)
        self._key_field.setText(key)
        self._key_field.blockSignals(False)

    def _on_key_changed(self, key: str):
        self.api_key_changed.emit(key)
        self._save_timer.start(500)

    def _save_api_key(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"gemini_api_key": self._key_field.text().strip()})
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gui_panels.py::test_settings_api_key_signal -v
```

Expected: 1 passed.

- [ ] **Step 5: Visual test — launch and click Settings**

```bash
python gui.py
```

Click the Settings button in the sidebar. Panel shows API key field, downloads path, guide button.

- [ ] **Step 6: Commit**

```bash
git add src/gui_panels.py tests/test_gui_panels.py
git commit -m "feat: add SettingsPanel with shared API key"
```

---

### Task 8: Checker Panel

**Files:**
- Modify: `src/gui_panels.py` — add `CheckerPanel`

**Interfaces:**
- Produces: `CheckerPanel(main_window)`
- Signals: `step_success()`, `continue_next()`
- Worker queue messages handled: `__DONE__`, `__SUCCESS__`, `__CHK_MOODLE_WAITING__`, `__CHK_H5P_WAITING__`, `__CHK_FILE_CHECKLIST__`

- [ ] **Step 1: Add `CheckerPanel` to `src/gui_panels.py`**

Append the following class to `src/gui_panels.py`:

```python
import asyncio, queue, threading


class CheckerPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event    = None
        self._file_checklist_event = None
        self._h5p_skip_flag      = [False]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Content Checker"))
        sub = QLabel("Verify that Moodle content exists in Brightspace — leave either URL blank to test just that side.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        layout.addSpacing(4)
        self._bs_entry = QLineEdit()
        self._bs_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/<id>/home")
        self._bs_entry.setFixedHeight(40)
        layout.addWidget(self._bs_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        layout.addWidget(self._moodle_entry)
        layout.addSpacing(14)

        from PySide6.QtWidgets import QCheckBox
        self._relink_cb   = QCheckBox("Re-link Moodle files in Brightspace after check")
        self._pdf_cb      = QCheckBox("Upload missing PDFs / files to Brightspace")
        self._h5p_cb      = QCheckBox("Upload H5P to Brightspace")
        self._relink_cb.setChecked(True)
        self._pdf_cb.setChecked(True)
        for cb in (self._relink_cb, self._pdf_cb, self._h5p_cb):
            layout.addWidget(cb)
            layout.addSpacing(4)
        layout.addSpacing(10)

        # Run buttons row
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        self._run_btn = QPushButton("Run Check")
        self._run_btn.setFixedHeight(42)
        self._run_btn.setIcon(make_icon("run", "#ffffff", 14))
        self._run_btn.clicked.connect(self._start_run)
        btn_row.addWidget(self._run_btn, 3)

        self._phase_b_btn = QPushButton("Phase B — H5P Upload")
        self._phase_b_btn.setProperty("variant", "phase-b")
        self._phase_b_btn.setFixedHeight(42)
        self._phase_b_btn.clicked.connect(self._start_phase_b)
        btn_row.addWidget(self._phase_b_btn, 1)
        layout.addLayout(btn_row)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        h5p_row = QHBoxLayout(); h5p_row.setSpacing(8)
        self._h5p_ready_btn = QPushButton("Ready — Download H5P")
        self._h5p_ready_btn.setProperty("variant", "success")
        self._h5p_ready_btn.setFixedHeight(38)
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn = QPushButton("Skip H5P")
        self._h5p_skip_btn.setProperty("variant", "secondary")
        self._h5p_skip_btn.setFixedWidth(120)
        self._h5p_skip_btn.setFixedHeight(38)
        self._h5p_skip_btn.hide()
        h5p_row.addWidget(self._h5p_ready_btn, 1)
        h5p_row.addWidget(self._h5p_skip_btn)
        layout.addLayout(h5p_row)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)

        self._log = LogWidget()
        layout.addWidget(self._log, 1)
        layout.addSpacing(8)

        # Downloads path (hidden until run completes)
        self._dl_label = QLabel()
        self._dl_label.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        self._dl_label.setProperty("role", "dim")
        self._dl_label.hide()
        layout.addWidget(self._dl_label)

        # Continue button (hidden until success)
        self._continue_btn = QPushButton("Continue to Unit Collector")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setIcon(make_icon("next", "#dde0ee", 14))
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

        # Load saved URLs
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_bs_url"):
            self._bs_entry.setText(cfg["chk_bs_url"])
        if cfg.get("chk_moodle_url"):
            self._moodle_entry.setText(cfg["chk_moodle_url"])

    def _run_worker(self, phase_b: bool = False):
        bs_url     = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not bs_url and not moodle_url:
            self._log.append_log("Paste at least one URL.", "warning")
            return
        if phase_b and not bs_url:
            self._log.append_log("Paste a Brightspace URL first.", "warning")
            return

        self._mw.save_config({"chk_bs_url": bs_url, "chk_moodle_url": moodle_url})

        import threading as _t
        moodle_ev = _t.Event(); h5p_ev = _t.Event(); file_ev = _t.Event()
        file_result = []
        skip_flag   = [False]
        self._moodle_ready_event   = moodle_ev
        self._h5p_ready_event      = h5p_ev
        self._file_checklist_event = file_ev
        self._h5p_skip_flag        = skip_flag

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        self._continue_btn.hide()
        self._dl_label.hide()

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._phase_b_btn.setEnabled(False)
        self._log.clear_log()

        q = self._log_queue

        def confirm(msg: str) -> bool:
            from PySide6.QtWidgets import QMessageBox
            result = [False]; ev = _t.Event()
            def ask():
                result[0] = QMessageBox.question(
                    self, "Continue?", msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) == QMessageBox.StandardButton.Yes
                ev.set()
            QTimer.singleShot(0, ask)
            ev.wait()
            return result[0]

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from content_checker import ContentChecker
                checker = ContentChecker(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put(("__CHK_MOODLE_WAITING__", "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put(("__CHK_H5P_WAITING__", skip_flag)),
                    file_checklist_event=file_ev,
                    on_file_checklist=lambda d: q.put(("__CHK_FILE_CHECKLIST__", (d, file_result, file_ev))),
                    confirm_fn=confirm,
                )
                checker.do_relink     = self._relink_cb.isChecked()
                checker.do_pdf_upload = self._pdf_cb.isChecked()
                checker.do_h5p_embed  = self._h5p_cb.isChecked()
                checker.file_checklist_result = file_result
                checker.h5p_skip_flag = skip_flag
                if phase_b:
                    checker.do_relink = False
                    checker.do_h5p_embed = True
                    checker.h5p_phase_b_only = True
                asyncio.run(checker.run())
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=False)

    def _start_phase_b(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=True)

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Run Check"); self._run_btn.setEnabled(True)
                    self._phase_b_btn.setEnabled(True)
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
                    dl = Path(__file__).parent.parent / "downloads"
                    self._dl_label.setText(f"Downloads: {dl}")
                    self._dl_label.show()
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    self._ready_btn.clicked.disconnect() if self._ready_btn.receivers(self._ready_btn.clicked) > 0 else None
                    self._ready_btn.clicked.connect(self._moodle_ready)
                    self._ready_btn.show()
                elif msg == "__CHK_H5P_WAITING__":
                    self._h5p_ready_btn.show(); self._h5p_skip_btn.show()
                    self._h5p_ready_btn.clicked.connect(self._h5p_ready)
                    self._h5p_skip_btn.clicked.connect(self._h5p_skip)
                elif msg == "__CHK_FILE_CHECKLIST__":
                    data_json, result_list, event = tag
                    from gui_dialogs import FileChecklistDialog
                    dlg = FileChecklistDialog(data_json, result_list, event, self)
                    dlg.exec()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._ready_btn.hide()
        if self._moodle_ready_event:
            self._moodle_ready_event.set()

    def _h5p_ready(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        if self._h5p_ready_event:
            self._h5p_ready_event.set()

    def _h5p_skip(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        self._h5p_skip_flag[0] = True
        if self._h5p_ready_event:
            self._h5p_ready_event.set()
```

- [ ] **Step 2: Write test**

```python
# Add to tests/test_gui_panels.py
def test_checker_panel_builds(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from unittest.mock import MagicMock
    from gui_panels import CheckerPanel
    mw = MagicMock()
    mw.chromium_ready = False
    mw.load_config.return_value = {}
    mw.save_config.return_value = None
    panel = CheckerPanel(mw)
    qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Run Check"
    assert panel._continue_btn.isHidden()
```

- [ ] **Step 3: Run test**

```bash
pytest tests/test_gui_panels.py::test_checker_panel_builds -v
```

Expected: 1 passed.

- [ ] **Step 4: Visual test**

```bash
python gui.py
```

Checker tab shows all inputs, checkboxes, Run Check + Phase B buttons, log area.

- [ ] **Step 5: Commit**

```bash
git add src/gui_panels.py tests/test_gui_panels.py
git commit -m "feat: add CheckerPanel with worker thread wiring"
```

---

### Task 9: Collector and Restyle Panels

**Files:**
- Modify: `src/gui_panels.py` — add `CollectorPanel`, `RestylePanel`, shared `_build_theme_swatches()`

**Interfaces:**
- Produces: `CollectorPanel(main_window)` — signals: `step_success()`, `continue_next()`
- Produces: `RestylePanel(main_window)` — signal: `step_success()`
- Produces: `_build_theme_swatches(parent_layout, selected_var, frames_dict)` — shared helper

- [ ] **Step 1: Add theme swatch helper and both panels to `src/gui_panels.py`**

Append to `src/gui_panels.py`:

```python
from PySide6.QtWidgets import QSpinBox, QButtonGroup
from PySide6.QtGui import QColor

# PAGE_THEMES duplicated here so gui_panels doesn't import gui.py
PAGE_THEMES = {
    "lake":     dict(primary="#005F63", mid="#2ECDDC", accent="#FF8204", circle="#005F63"),
    "sky":      dict(primary="#2ECDDC", mid="#6EDFE8", accent="#005F63", circle="#2ECDDC"),
    "sunset":   dict(primary="#FF8204", mid="#FFA340", accent="#005F63", circle="#FF8204"),
    "peach":    dict(primary="#DE4F3D", mid="#E87A68", accent="#FF8204", circle="#DE4F3D"),
    "cherry":   dict(primary="#E10040", mid="#FF3366", accent="#FF8204", circle="#E10040"),
    "cabernet": dict(primary="#782434", mid="#A03C54", accent="#DE4F3D", circle="#782434"),
    "lavender": dict(primary="#50037F", mid="#8B3FC0", accent="#2ECDDC", circle="#50037F"),
    "lilac":    dict(primary="#9B5CB8", mid="#CA9CE4", accent="#2ECDDC", circle="#CA9CE4"),
    "charcoal": dict(primary="#50534C", mid="#7A7D74", accent="#2ECDDC", circle="#50534C"),
}


def _build_theme_swatches(parent_layout: QVBoxLayout) -> tuple[dict, list]:
    """Returns (frames_dict keyed by theme name, [selected_name_ref])."""
    selected = ["lake"]
    frames: dict[str, QWidget] = {}

    row_widget = QWidget()
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)

    def _select(name: str):
        old = selected[0]
        if old in frames:
            frames[old].setStyleSheet(
                f"background:{PAGE_THEMES[old]['circle']};border-radius:13px;"
                f"border:2px solid transparent;"
            )
        selected[0] = name
        frames[name].setStyleSheet(
            f"background:{PAGE_THEMES[name]['circle']};border-radius:13px;"
            f"border:2px solid #ffffff;"
        )

    for name, theme in PAGE_THEMES.items():
        swatch = QPushButton()
        swatch.setFixedSize(26, 26)
        swatch.setStyleSheet(
            f"background:{theme['circle']};border-radius:13px;border:2px solid transparent;"
        )
        swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        swatch.clicked.connect(lambda _=False, n=name: _select(n))
        frames[name] = swatch
        row.addWidget(swatch)

    row.addStretch()
    parent_layout.addWidget(row_widget)
    _select("lake")
    return frames, selected


class CollectorPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Unit Collector"))
        sub = QLabel("Scrapes all topic pages from a unit and combines them into one collapsible HTML file.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("PAGE THEME"))
        layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(layout)
        layout.addSpacing(14)

        layout.addWidget(_form_label("BRIGHTSPACE UNIT URL"))
        layout.addSpacing(4)
        self._unit_entry = QLineEdit()
        self._unit_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…")
        self._unit_entry.setFixedHeight(40)
        layout.addWidget(self._unit_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("TARGET PAGE URL  (empty Brightspace page you created)"))
        layout.addSpacing(4)
        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/topics/…/View")
        self._target_entry.setFixedHeight(40)
        layout.addWidget(self._target_entry)
        layout.addSpacing(12)

        par_row = QHBoxLayout()
        par_row.addWidget(_form_label("PARALLEL PAGES"))
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 10)
        self._parallel_spin.setValue(3)
        self._parallel_spin.setFixedWidth(60)
        par_row.addWidget(self._parallel_spin)
        par_row.addStretch()
        layout.addLayout(par_row)
        layout.addSpacing(14)

        self._run_btn = QPushButton("Collect & Assemble")
        self._run_btn.setFixedHeight(42)
        self._run_btn.setIcon(make_icon("run", "#ffffff", 14))
        self._run_btn.clicked.connect(self._start_run)
        layout.addWidget(self._run_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)
        layout.addSpacing(8)

        self._continue_btn = QPushButton("Continue to Page Changer")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setIcon(make_icon("next", "#dde0ee", 14))
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        unit_url   = self._unit_entry.text().strip()
        target_url = self._target_entry.text().strip()
        if not unit_url:
            self._log.append_log("Paste a Brightspace unit URL first.", "warning"); return
        if not target_url:
            self._log.append_log("Paste the target page URL first.", "warning"); return

        theme_name   = self._selected_theme[0]
        theme_colors = PAGE_THEMES[theme_name]
        parallel     = self._parallel_spin.value()

        style_ref_path = Path(__file__).parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._continue_btn.hide()
        self._log.clear_log()

        q = self._log_queue

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from unit_collector import run as collector_run
                asyncio.run(collector_run(
                    unit_url=unit_url,
                    target_url=target_url,
                    theme_name=theme_name,
                    theme_colors=theme_colors,
                    gemini_api_key=self._mw.gemini_api_key,
                    style_reference_html=style_reference_html,
                    parallel_pages=parallel,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                ))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Collect & Assemble")
                    self._run_btn.setEnabled(True)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass


class RestylePanel(QWidget):
    step_success = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._response_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Page Changer"))
        sub = QLabel("Pick an OC brand colour theme, paste a Brightspace page or section URL, and let Gemini restyle it.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("PAGE THEME"))
        layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(layout)
        layout.addSpacing(14)

        layout.addWidget(_form_label("BRIGHTSPACE PAGE URL"))
        layout.addSpacing(4)

        url_row = QHBoxLayout(); url_row.setSpacing(8)
        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/home/…")
        self._url_entry.setFixedHeight(42)
        url_row.addWidget(self._url_entry, 1)

        self._run_btn = QPushButton("Start")
        self._run_btn.setFixedSize(110, 42)
        self._run_btn.setIcon(make_icon("run", "#ffffff", 14))
        self._run_btn.clicked.connect(self._start_run)
        url_row.addWidget(self._run_btn)
        layout.addLayout(url_row)
        layout.addSpacing(12)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

        # Load saved URL
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("automator_url"):
            self._url_entry.setText(cfg["automator_url"])

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning"); return
        url = self._url_entry.text().strip()
        if not url:
            self._log.append_log("Paste a Brightspace URL first.", "warning"); return

        style_ref_path = Path(__file__).parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._log.clear_log()

        q  = self._log_queue
        rq = self._response_queue

        def on_pages_found(pages):
            q.put(("__PAGES__", pages))
            return rq.get(timeout=300)

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                import sys as _sys
                _sys.modules.pop("automator", None)
                from automator import run as automator_run
                asyncio.run(automator_run(
                    url=url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    gemini_api_key=self._mw.gemini_api_key,
                    style_reference_html=style_reference_html,
                    theme_name=self._selected_theme[0],
                    on_pages_found=on_pages_found,
                ))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Start"); self._run_btn.setEnabled(True)
                    if hasattr(self._mw, "save_config"):
                        self._mw.save_config({"automator_url": self._url_entry.text().strip()})
                elif msg == "__PAGES__":
                    from gui_dialogs import PagesDialog
                    dlg = PagesDialog(tag, self)
                    if dlg.exec():
                        self._response_queue.put(dlg.result_value())
                    else:
                        self._response_queue.put((0, len(tag)))
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
```

- [ ] **Step 2: Write tests**

```python
# Add to tests/test_gui_panels.py
def test_collector_panel_builds(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Collect & Assemble"
    assert panel._continue_btn.isHidden()

def test_restyle_panel_builds(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import RestylePanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = RestylePanel(mw); qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Start"
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_gui_panels.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Visual test — all three panels**

```bash
python gui.py
```

Click each step in sidebar. All three panels render. Theme swatches are consistent between Collect and Restyle.

- [ ] **Step 5: Commit**

```bash
git add src/gui_panels.py tests/test_gui_panels.py
git commit -m "feat: add CollectorPanel and RestylePanel with shared theme swatch helper"
```

---

### Task 10: Dialogs (`src/gui_dialogs.py`)

**Files:**
- Create: `src/gui_dialogs.py`

**Interfaces:**
- Produces: `FileChecklistDialog(data_json, result_list, event, parent)` — QDialog
- Produces: `PagesDialog(pages, parent)` — QDialog; `.result_value() -> (start_idx, count)`
- Produces: `UpdateDialog(release, parent)` — QDialog

- [ ] **Step 1: Create `src/gui_dialogs.py`**

```python
# src/gui_dialogs.py
import json
import threading
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QCheckBox, QLineEdit, QFrame,
)
from PySide6.QtCore import Qt, QTimer

from gui_styles import BG, PANEL, TEXT_SEC, TEXT_FAINT, OC_TEAL, DONE, BORDER
from gui_log import LogWidget


class FileChecklistDialog(QDialog):
    def __init__(self, data_json: str, result_list: list, event: threading.Event, parent=None):
        super().__init__(parent)
        self._result_list = result_list
        self._event       = event
        self._files       = json.loads(data_json)
        self._checkboxes: list[tuple[QCheckBox, dict]] = []
        self.setWindowTitle("Missing Files — Select to Download")
        self.setMinimumSize(560, 500)
        self.setModal(True)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)

        layout.addWidget(QLabel(f"{len(self._files)} file(s) missing from Brightspace",
                                styleSheet="font-size:15px;font-weight:600;"))
        sub = QLabel("Files will be downloaded from Moodle and uploaded to the matching section.")
        sub.setWordWrap(True); sub.setStyleSheet(f"color:{TEXT_SEC};")
        layout.addWidget(sub)
        layout.addSpacing(10)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget(); scroll.setWidget(container)
        cl = QVBoxLayout(container); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(2)

        cur_section = None
        for f in self._files:
            sec = f.get("section") or "Other"
            if sec != cur_section:
                cur_section = sec
                lbl = QLabel(f"— {sec} —")
                lbl.setStyleSheet(f"color:{TEXT_FAINT};font-size:11px;margin-top:8px;")
                cl.addWidget(lbl)
            cb = QCheckBox(f["name"]); cb.setChecked(True)
            cb.stateChanged.connect(self._update_count)
            self._checkboxes.append((cb, f))
            cl.addWidget(cb)

        cl.addStretch()
        layout.addWidget(scroll, 1)

        # Toggle row
        tog = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_all.setProperty("variant", "secondary"); sel_all.setFixedHeight(32)
        sel_all.clicked.connect(lambda: [cb.setChecked(True) for cb, _ in self._checkboxes])
        desel = QPushButton("Deselect All")
        desel.setProperty("variant", "secondary"); desel.setFixedHeight(32)
        desel.clicked.connect(lambda: [cb.setChecked(False) for cb, _ in self._checkboxes])
        tog.addWidget(sel_all); tog.addWidget(desel); tog.addStretch()
        layout.addLayout(tog)

        self._count_lbl = QLabel(); layout.addWidget(self._count_lbl)

        btn_row = QHBoxLayout()
        self._dl_btn = QPushButton(""); self._dl_btn.setFixedHeight(40)
        self._dl_btn.clicked.connect(self._download)
        btn_row.addWidget(self._dl_btn, 1)
        skip = QPushButton("Skip All"); skip.setProperty("variant", "secondary")
        skip.setFixedSize(100, 40); skip.clicked.connect(self._skip)
        btn_row.addWidget(skip)
        layout.addLayout(btn_row)

        self._update_count()

    def _update_count(self, *_):
        n = sum(1 for cb, _ in self._checkboxes if cb.isChecked())
        self._dl_btn.setText(f"Download {n} Selected")
        self._count_lbl.setText(f"{n} of {len(self._files)} selected")
        self._count_lbl.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;")

    def _download(self):
        selected = [f for cb, f in self._checkboxes if cb.isChecked()]
        self._result_list.clear(); self._result_list.extend(selected)
        self.accept(); self._event.set()

    def _skip(self):
        self._result_list.clear(); self.accept(); self._event.set()

    def reject(self):
        self._event.set(); super().reject()


class PagesDialog(QDialog):
    def __init__(self, pages: list, parent=None):
        super().__init__(parent)
        self._pages = pages
        self._start = 0
        self._count = len(pages)
        self.setWindowTitle("Pages Found")
        self.setFixedSize(460, 420)
        self.setModal(True)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        layout.addWidget(QLabel(f"Found {len(self._pages)} pages in this section",
                                styleSheet="font-size:15px;font-weight:600;"))
        layout.addSpacing(10)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFixedHeight(160)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget(); scroll.setWidget(container)
        cl = QVBoxLayout(container); cl.setContentsMargins(0, 0, 0, 0)
        for i, p in enumerate(self._pages, 1):
            lbl = QLabel(f"{i}.  {p['label']}")
            lbl.setStyleSheet(f"color:{TEXT_SEC};font-size:12px;"); cl.addWidget(lbl)
        cl.addStretch()
        layout.addWidget(scroll)
        layout.addSpacing(16)

        fields = QHBoxLayout(); fields.setSpacing(12)
        fields.addWidget(QLabel("Start from page:"))
        self._start_edit = QLineEdit("1"); self._start_edit.setFixedWidth(60)
        fields.addWidget(self._start_edit)
        fields.addWidget(QLabel("How many:"))
        self._count_edit = QLineEdit(str(len(self._pages))); self._count_edit.setFixedWidth(60)
        fields.addWidget(self._count_edit); fields.addStretch()
        layout.addLayout(fields)
        layout.addSpacing(16)

        run = QPushButton("Run"); run.setFixedHeight(40); run.clicked.connect(self._on_run)
        layout.addWidget(run)

    def _on_run(self):
        try:
            self._start = max(1, int(self._start_edit.text())) - 1
            self._count = max(1, int(self._count_edit.text()))
        except ValueError:
            self._start, self._count = 0, len(self._pages)
        self.accept()

    def result_value(self) -> tuple[int, int]:
        return self._start, self._count


class UpdateDialog(QDialog):
    def __init__(self, release: dict, parent=None):
        super().__init__(parent)
        self._release = release
        self._mw      = parent
        self.setWindowTitle("Update available")
        self.setFixedSize(500, 380)
        self.setModal(True)
        self._build()

    def _build(self):
        import sys
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        layout.addWidget(QLabel(f"New version available: {self._release['tag']}",
                                styleSheet="font-size:15px;font-weight:600;"))
        notes = LogWidget()
        notes.setReadOnly(True); notes.setFixedHeight(160)
        notes.append_log(self._release.get("body", ""), "info")
        layout.addWidget(notes, 1)

        self._status = QLabel()
        self._status.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        skip = QPushButton("Skip this version"); skip.setProperty("variant", "secondary")
        skip.clicked.connect(self._skip)
        later = QPushButton("Remind me later"); later.setProperty("variant", "secondary")
        later.clicked.connect(self.reject)
        self._update_btn = QPushButton("Update Now")
        has_asset = bool(self._release.get("asset_url")) and sys.platform == "win32"
        if not has_asset:
            self._update_btn.setEnabled(False)
            self._status.setText("No installer found for this platform.")
        self._update_btn.clicked.connect(self._do_update)
        btn_row.addWidget(skip); btn_row.addWidget(later); btn_row.addWidget(self._update_btn)
        layout.addLayout(btn_row)

    def _skip(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"skipped_update_tag": self._release["tag"]})
        self.reject()

    def _do_update(self):
        import sys, tempfile, threading, subprocess
        from pathlib import Path
        from update_checker import download_asset

        if not self._release.get("asset_url"):
            webbrowser.open(self._release.get("html_url", ""))
            self.accept(); return

        self._update_btn.setEnabled(False)
        self._status.setText("Downloading…")

        def worker():
            try:
                tmp = Path(tempfile.gettempdir()) / self._release["asset_name"]
                download_asset(
                    self._release["asset_url"], tmp,
                    progress_cb=lambda pct: QTimer.singleShot(
                        0, lambda: self._status.setText(f"Downloading… {pct}%")
                    ),
                )
                QTimer.singleShot(0, lambda: self._status.setText("Installing…"))
                subprocess.Popen(
                    [str(tmp), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    close_fds=True,
                )
                QTimer.singleShot(0, lambda: self._mw.close() if self._mw else None)
            except Exception as e:
                QTimer.singleShot(0, lambda: self._status.setText(f"Update failed: {e}"))

        threading.Thread(target=worker, daemon=True).start()
```

- [ ] **Step 2: Write a smoke test**

```python
# Add to tests/test_gui_panels.py
def test_pages_dialog_result(qtbot):
    from gui_dialogs import PagesDialog
    pages = [{"label": "Page One"}, {"label": "Page Two"}]
    dlg = PagesDialog(pages)
    qtbot.addWidget(dlg)
    assert dlg._start_edit.text() == "1"
    assert dlg._count_edit.text() == "2"
    # Simulate accept
    dlg._on_run()
    assert dlg.result_value() == (0, 2)  # 1-indexed → 0-indexed
```

- [ ] **Step 3: Run test**

```bash
pytest tests/test_gui_panels.py::test_pages_dialog_result -v
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add src/gui_dialogs.py tests/test_gui_panels.py
git commit -m "feat: add FileChecklistDialog, PagesDialog, UpdateDialog"
```

---

### Task 11: Step Locking — `__SUCCESS__` Signal

**Files:**
- Modify: `src/content_checker.py` — **read** only to understand where to emit; signal goes through the log queue

**Note:** `content_checker.py` is read-only. The `__SUCCESS__` message is emitted by the **worker closure** in `CheckerPanel._run_worker()` — not by the backend. Add it after `asyncio.run(checker.run())` completes without exception.

- [ ] **Step 1: Modify the worker closure in `CheckerPanel._run_worker` in `src/gui_panels.py`**

Find the `worker()` function inside `CheckerPanel._run_worker`. After `asyncio.run(checker.run())` and before the `except`, add:

```python
                asyncio.run(checker.run())
                q.put(("__SUCCESS__", ""))   # ← ADD THIS LINE
            except Exception as e:
```

Do the same for `CollectorPanel._start_run`'s worker:

```python
                asyncio.run(collector_run(...))
                q.put(("__SUCCESS__", ""))   # ← ADD THIS LINE
            except Exception as e:
```

- [ ] **Step 2: Verify unlock flow manually**

```bash
python gui.py
```

Run the Checker (even with invalid URLs — it will error). On success only: step 2 should unlock. On error: step 2 stays locked. Verify both.

- [ ] **Step 3: Commit**

```bash
git add src/gui_panels.py
git commit -m "feat: emit __SUCCESS__ to unlock next step after clean run"
```

---

### Task 12: Final Wiring and Smoke Test

**Files:**
- Modify: `gui.py` — fix any import issues found during smoke test

- [ ] **Step 1: Full end-to-end smoke test**

```bash
python gui.py
```

Checklist:
- [ ] App launches, sidebar shows 3 steps + Settings button
- [ ] Step 1 (Checker) is active on launch, steps 2 and 3 are locked (padlock icon)
- [ ] Clicking locked steps does nothing
- [ ] Clicking Settings shows Settings panel with API key field
- [ ] Typing in API key field — saves to `user_config.json` after 500ms
- [ ] Restart app — API key is restored
- [ ] Checker tab: inputs, checkboxes, Run Check button, Phase B button all render
- [ ] Collector tab: theme swatches + URL fields render
- [ ] Restyle tab: theme swatches + URL field render
- [ ] Ctrl+Scroll in any log area zooms font and shows badge
- [ ] No emoji visible anywhere in UI

- [ ] **Step 2: Fix any import errors found in Step 1**

Common issues to look for:
- `from gui_panels import ...` fails if `gui_panels.py` imports something not yet installed
- `from api_config import ...` may not exist on fresh machine — the `try/except ImportError` blocks handle this
- On Windows, `os.startfile(...)` requires the path to exist — the downloads path may not exist yet; guard it:

```python
# In SettingsPanel._build, fix open_btn:
open_btn.clicked.connect(
    lambda: os.startfile(str(downloads_path) if downloads_path.exists()
                          else str(downloads_path.parent))
)
```

- [ ] **Step 3: Commit all fixes**

```bash
git add gui.py src/gui_panels.py src/gui_dialogs.py
git commit -m "fix: resolve import and path issues found in smoke test"
```

---

### Task 13: PyInstaller and Dependency Cleanup

**Files:**
- Modify: `installer/brightspace_automator.spec`
- Modify: `installer/brightspace_automator_mac.spec`

- [ ] **Step 1: Update Windows spec for PySide6**

Open `installer/brightspace_automator.spec`. Replace any `customtkinter` hidden imports with PySide6. Add:

```python
hiddenimports=[
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PIL.ImageQt',
],
```

Remove any references to `customtkinter` or `CTkMessagebox`.

- [ ] **Step 2: Add PySide6 data files to spec**

In the `datas` list, add the PySide6 Qt plugins path. On Windows this is typically auto-discovered by PyInstaller's PySide6 hook, but verify:

```bash
python -c "import PySide6; print(PySide6.__file__)"
```

If PyInstaller 6.x is installed, the PySide6 hook handles this automatically — no manual datas entry needed.

- [ ] **Step 3: Test build (Windows)**

```bash
pip install pyinstaller
pyinstaller installer/brightspace_automator.spec --noconfirm
```

Check `dist/` for the output executable. Run it, verify app launches.

- [ ] **Step 4: Update Mac spec equivalently**

Same changes to `installer/brightspace_automator_mac.spec`.

- [ ] **Step 5: Commit**

```bash
git add installer/
git commit -m "chore: update PyInstaller specs for PySide6"
```

---

### Task 14: Final Branch Commit and Share

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Push branch for others to preview**

```bash
git push -u origin feature/gui-redesign
```

Others can preview with:
```bash
git fetch origin
git checkout feature/gui-redesign
pip install -r requirements.txt
python gui.py
```

- [ ] **Step 3: Final commit**

```bash
git add .
git commit -m "feat: complete PySide6 GUI redesign on feature/gui-redesign branch"
```

---

## Self-Review Notes

**Spec coverage check:**
- Sidebar step rail ✓ (Task 5, 6)
- QPainter icons ✓ (Task 3)
- LogWidget with zoom ✓ (Task 4)
- Single API key in Settings ✓ (Task 7)
- Step locking + `__SUCCESS__` signal ✓ (Task 11)
- "Continue to next step" buttons ✓ (Tasks 8, 9)
- Downloads path in Checker after run ✓ (Task 8)
- File checklist dialog ✓ (Task 10)
- Pages dialog ✓ (Task 10)
- Update dialog ✓ (Task 10)
- Chromium setup dialog ✓ (Task 6)
- Theme swatches deduplicated ✓ (Task 9 — `_build_theme_swatches`)
- No emoji ✓ (enforced throughout)
- Branch strategy ✓ (Tasks 1, 14)

**Type consistency:** `make_icon`/`make_pixmap` signatures used consistently. `gemini_api_key` accessed as `self._mw.gemini_api_key` everywhere. `load_config`/`save_config` called as `self._mw.load_config()` / `self._mw.save_config(dict)` consistently.

**Gap found and added:** `gui_ctk_backup.py` backup step added in Task 6 so the old file is never lost.
