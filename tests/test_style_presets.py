"""Exercise real design references and the prompt actually sent to the API."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import sys

import pytest

sys.path.insert(0, "src")

import ai_styler
import style_presets


ROOT = Path(__file__).resolve().parents[1]
THEMES = [path.stem for path in (ROOT / "prompts").glob("*.txt")]


@pytest.mark.parametrize("preset", ["calm", "classic"])
def test_reference_loads_from_checkout(preset):
    reference = style_presets.load_style_reference(preset)
    assert bool("BPA: CLASSIC" in reference) == (preset == "classic")
    assert bool("background: linear-gradient" in reference) == (preset == "classic")


@pytest.mark.parametrize("preset", ["calm", "classic"])
def test_installed_app_loads_selected_reference_from_bundle(monkeypatch, tmp_path, preset):
    templates = tmp_path / "templates"
    templates.mkdir()
    filename = "style_reference_classic.html" if preset == "classic" else "style_reference.html"
    expected = (ROOT / "templates" / filename).read_text(encoding="utf-8")
    (templates / filename).write_text(expected, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    # Frozen modules live directly in the bundle, unlike src/ in a checkout.
    monkeypatch.setattr(style_presets, "__file__", str(tmp_path / "style_presets.py"))

    assert style_presets.load_style_reference(preset) == expected


def test_missing_classic_reference_does_not_silently_use_calm(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(FileNotFoundError):
        style_presets.load_style_reference("classic")


@pytest.mark.parametrize("panel_name", ["restyle", "collector"])
def test_missing_reference_stops_gui_before_starting_worker(qtbot, monkeypatch, panel_name):
    from panels import collector_panel, restyle_panel

    module = restyle_panel if panel_name == "restyle" else collector_panel
    panel_class = module.RestylePanel if panel_name == "restyle" else module.CollectorPanel
    window = MagicMock()
    window.chromium_ready = True
    window.load_config.return_value = {}
    panel = panel_class(window)
    qtbot.addWidget(panel)
    panel._style_preset.setCurrentIndex(panel._style_preset.findData("classic"))
    if panel_name == "restyle":
        panel._url_entry.setText("https://example.test/d2l/home/123")
    else:
        panel._unit_entry.setText("https://example.test/d2l/home/123")
        panel._target_entry.setText("https://example.test/d2l/home/123")

    def missing_reference(preset):
        assert preset == "classic"
        raise FileNotFoundError("missing bundled template")

    monkeypatch.setattr(module, "load_style_reference", missing_reference)
    thread = MagicMock()
    monkeypatch.setattr(module.threading, "Thread", thread)
    log = MagicMock()
    monkeypatch.setattr(panel._log, "append_log", log)
    panel._start_run()

    thread.assert_not_called()
    assert "selected page design could not be loaded" in log.call_args.args[0]
    assert panel._run_btn.isEnabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("preset", ["calm", "classic"])
async def test_sent_prompt_uses_only_selected_design(monkeypatch, theme, preset):
    sent = []
    source = '<h1>Weekly resources</h1><p><a href="/notes.pdf">Lecture notes</a></p>'
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=source)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=200),
    )

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get_final_message(self):
            return response

    def stream(**kwargs):
        sent.append(kwargs["messages"][0]["content"])
        return Stream()

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    monkeypatch.setattr(ai_styler.anthropic, "AsyncAnthropic", lambda **kwargs: client)
    reference = style_presets.load_style_reference(preset)
    result, usage = await ai_styler.apply_style(source, reference, theme, "test-key")

    assert result and usage
    assert len(sent) == 1
    prompt = sent[0]
    assert source in prompt
    assert reference in prompt
    assert f"Apply the {theme.upper()} theme" in prompt
    if preset == "classic":
        assert "CLASSIC PRESET DESIGN" in prompt
        assert "linear-gradient from --primary to --mid" in prompt
        assert "large white uppercase page title" in prompt
        assert "spacious rounded .action-card sections" in prompt
        assert "CALM PRESET" not in prompt
        assert "Use a calm hierarchy" not in prompt
        assert "RESOURCE DIRECTORY OVERRIDE" not in prompt
        assert "accessible dark orange action colour" not in prompt
    else:
        assert "CALM PRESET READABILITY OVERRIDE" in prompt
        assert "Use a calm hierarchy" in prompt
        assert "RESOURCE DIRECTORY OVERRIDE" in prompt
        assert "CLASSIC PRESET DESIGN" not in prompt
