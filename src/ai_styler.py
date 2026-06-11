import google.generativeai as genai
from typing import Optional

PROMPT_TEMPLATE = """You are a front-end developer.

SOURCE PAGE: This page contains the content that must be preserved.
STYLE REFERENCE: This page demonstrates the desired visual style.

Requirements:
- Preserve ALL content, links, and functionality from SOURCE PAGE.
- Use STYLE REFERENCE as the design inspiration.
- Match spacing, card design, typography, colors, buttons, and section layout.
- Do NOT remove any content.
- If SOURCE PAGE lacks structure, reorganize using patterns from STYLE REFERENCE.
- Return ONLY the complete updated HTML. No explanation, no markdown fences, no preamble.

=== SOURCE HTML ===
{source_html}

=== STYLE REFERENCE HTML ===
{style_reference_html}

=== ACTIVE COLOR THEME ===
Primary color: {primary_color}
"""

def apply_style(
    source_html: str,
    style_reference_html: str,
    primary_color: str,
    api_key: str,
    log_callback=None
) -> Optional[str]:
    """
    Call Google Gemini API to restyle HTML based on a reference page.
    
    Args:
        source_html: The original HTML to restyle
        style_reference_html: HTML demonstrating the desired style
        primary_color: Primary color from the active theme (e.g., "#2D8CFF")
        api_key: Google Generative AI API key
        log_callback: Optional callback for logging (receives msg, level)
    
    Returns:
        Restyled HTML string, or None if the API call fails
    """
    def log(msg, level="info"):
        if log_callback:
            log_callback(msg, level)

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = PROMPT_TEMPLATE.format(
            source_html=source_html,
            style_reference_html=style_reference_html,
            primary_color=primary_color
        )

        log("🤖 Sending to Gemini AI...", "info")
        response = model.generate_content(prompt)
        result = response.text.strip()

        # Strip markdown fences if model accidentally added them
        if result.startswith("```"):
            result = result.split("```")[1]
            if result.startswith("html"):
                result = result[4:]
            result = result.strip()

        log("✅ AI styling complete", "success")
        return result

    except Exception as e:
        log(f"❌ Gemini API error: {e}", "error")
        return None
