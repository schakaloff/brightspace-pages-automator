## 2024-03-24 - Add focus states for interactive widgets
**Learning:** PySide/Qt application accessibility via QSS (Qt Style Sheets) isn't automatic; keyboard focus indicators must be manually set for most widgets. TextEdit, SpinBox, ToolButtons, and ComboBoxes don't inherit the standard web-like focus rings.
**Action:** Always check `src/gui_styles.py` when auditing Qt interfaces for accessibility to ensure all actionable elements have a `:focus` property defined.
