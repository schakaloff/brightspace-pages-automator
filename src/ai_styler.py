from google import genai
from pathlib import Path
from typing import Optional

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(theme_name: str) -> str:
    path = _PROMPTS_DIR / f"{theme_name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    fallback = _PROMPTS_DIR / "blue.txt"
    return fallback.read_text(encoding="utf-8") if fallback.exists() else ""


def apply_style(
    source_html: str,
    style_reference_html: str,
    theme_name: str,
    api_key: str,
    log_callback=None,
) -> Optional[str]:
    def log(msg, level="info"):
        if log_callback:
            log_callback(msg, level)

    try:
        prompt_template = _load_prompt(theme_name)
        if not prompt_template:
            log(f"❌ No prompt file found for theme '{theme_name}'", "error")
            return None

        prompt = prompt_template.format(
            source_html=source_html,
            style_reference_html=style_reference_html or "(no reference provided)",
        )

        log(f"🤖 Sending to Gemini 3.5 Flash (theme: {theme_name})...", "info")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
        )
        result = response.text.strip()

        # Strip markdown fences if model accidentally added them
        if result.startswith("```"):
            lines = result.splitlines()
            start = 1 if lines[0].startswith("```") else 0
            end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            result = "\n".join(lines[start:end]).strip()

        log(f"✅ AI styling complete ({len(result):,} chars)", "success")
        return result

    except Exception as e:
        import traceback
        log(f"❌ Gemini API error: {e}", "error")
        log(traceback.format_exc(), "error")
        return None
