import time
from google import genai
from google.genai import errors as genai_errors
from pathlib import Path
from typing import Optional

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_MODEL = "gemini-3.5-flash"
_MAX_RETRIES = 3
_RETRY_DELAY = 8  # seconds between retries on 503


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

    prompt_template = _load_prompt(theme_name)
    if not prompt_template:
        log(f"❌ No prompt file for theme '{theme_name}'", "error")
        return None

    prompt = prompt_template.format(
        source_html=source_html,
        style_reference_html=style_reference_html or "",
    )

    client = genai.Client(api_key=api_key)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            log(f"🤖 Gemini 3.5 Flash — attempt {attempt}/{_MAX_RETRIES} (theme: {theme_name})", "info")
            response = client.models.generate_content(model=_MODEL, contents=prompt)
            result = response.text.strip()

            if result.startswith("```"):
                lines = result.splitlines()
                start = 1 if lines[0].startswith("```") else 0
                end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                result = "\n".join(lines[start:end]).strip()

            log(f"✅ Done ({len(result):,} chars)", "success")
            return result

        except genai_errors.ServerError as e:
            if attempt < _MAX_RETRIES:
                log(f"⚠ Server busy (503) — retrying in {_RETRY_DELAY}s...", "warning")
                time.sleep(_RETRY_DELAY)
            else:
                log(f"❌ Gemini unavailable after {_MAX_RETRIES} attempts: {e}", "error")
                return None

        except Exception as e:
            log(f"❌ Gemini error: {e}", "error")
            return None
