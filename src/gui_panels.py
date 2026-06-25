# Backward-compat shim — real code lives in src/panels/
from panels.settings_panel import SettingsPanel
from panels.checker_panel import CheckerPanel
from panels.collector_panel import CollectorPanel
from panels.restyle_panel import RestylePanel
from panels._shared import (
    _divider, _form_label, _section_header,
    PAGE_THEMES, _build_theme_swatches,
)

__all__ = [
    "SettingsPanel", "CheckerPanel", "CollectorPanel", "RestylePanel",
    "_divider", "_form_label", "_section_header",
    "PAGE_THEMES", "_build_theme_swatches",
]
