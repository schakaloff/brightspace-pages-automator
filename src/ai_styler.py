import re
import sys
import time
from pathlib import Path
from typing import Optional

_COLOR_PROP_RE = re.compile(r'(?:^|(?<=;))\s*(?:color|background-color)\s*:[^;]*', re.IGNORECASE)


def _strip_color_from_style(style: str) -> str:
    cleaned = _COLOR_PROP_RE.sub('', style)
    # normalise leftover semicolons
    parts = [p.strip() for p in cleaned.split(';') if p.strip()]
    return '; '.join(parts)


def _clean_html(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # strip tags that add no content value, but preserve Kaltura player scripts
    for tag in soup.find_all(["script", "style", "meta", "link", "head"]):
        if tag.name == "script":
            src = tag.get("src", "")
            text = tag.get_text()
            if "kaltura" in src.lower() or "KalturaPlayer" in text or "kalturaPlayer" in text:
                continue
        tag.decompose()

    # remove all data-* and aria-* attributes, plus common Brightspace noise
    noise_attrs = {"data-d2l-uid", "data-when-user-interacts", "data-placeholder"}
    for tag in soup.find_all(True):
        attrs_to_remove = [
            a for a in list(tag.attrs)
            if a.startswith("data-") or a.startswith("aria-") or a in noise_attrs
        ]
        for a in attrs_to_remove:
            del tag.attrs[a]

        # strip inline color/background-color so the theme controls all colours
        if tag.get('style'):
            cleaned = _strip_color_from_style(tag['style'])
            if cleaned:
                tag['style'] = cleaned
            else:
                del tag.attrs['style']

        # strip legacy <font color="..."> attribute
        if tag.name == 'font' and tag.get('color'):
            del tag.attrs['color']

    # collapse empty tags that carry no content (spans, divs with no text/children)
    for tag in soup.find_all(["span", "div"]):
        if tag.get("id", "").startswith("kaltura_player_"):
            continue
        if not tag.get_text(strip=True) and not tag.find(["img", "iframe", "video", "table"]):
            tag.unwrap()

    # return just the body content if present, else the whole cleaned string
    body = soup.find("body")
    result = body.decode_contents() if body else str(soup)
    return result.strip()


def _restore_kaltura_sizing(cleaned_html: str, styled_html: str, log=None) -> str:
    """Kaltura's player library sizes itself off the inline width/height style
    on its `id="kaltura_player_*"` container div. Claude's restyle rewrite is
    generative and sometimes drops that inline style in favour of a generic
    responsive-iframe CSS pattern the player can't use (its internal UI is a
    normal flex child, not an absolutely-positioned fill-parent element), which
    collapses the whole player to zero height and the video never loads.
    Force the original inline style back onto any such container Claude's
    output still contains, so the player keeps a real pixel size."""
    from bs4 import BeautifulSoup

    id_re = re.compile(r"^kaltura_player_")

    source_soup = BeautifulSoup(cleaned_html, "lxml")
    original_styles = {
        tag["id"]: tag.get("style", "")
        for tag in source_soup.find_all(id=id_re)
    }
    if not original_styles:
        return styled_html

    result_soup = BeautifulSoup(styled_html, "lxml")
    restored = 0
    for player_id, style in original_styles.items():
        tag = result_soup.find(id=player_id)
        if tag is None:
            continue
        if tag.get("style", "") != style:
            if style:
                tag["style"] = style
            elif "style" in tag.attrs:
                del tag.attrs["style"]
            restored += 1

    if not restored:
        return styled_html

    if log:
        log(
            f"🛠 Restored original size on {restored} Kaltura player container(s) "
            "the rewrite had resized",
            "warning",
        )

    body = result_soup.find("body")
    return (body.decode_contents() if body else str(result_soup)).strip()


_PROMPTS_DIR = (
    Path(sys._MEIPASS) / "prompts"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent / "prompts"
)
DEFAULT_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 8192
_MAX_RETRIES = 3
_RETRY_DELAY = 8  # seconds between retries on overload

# USD per 1M tokens (input, output) — from Anthropic's published pricing.
_PRICING_USD_PER_MTOK = {
    "claude-opus-4-5":  (5.00, 25.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
USD_TO_CAD = 1.38  # approximate — update if the exchange rate shifts meaningfully


def _cost_cad(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING_USD_PER_MTOK.get(model, _PRICING_USD_PER_MTOK[DEFAULT_MODEL])
    usd = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return usd * USD_TO_CAD


def _load_prompt(theme_name: str) -> str:
    path = _PROMPTS_DIR / f"{theme_name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    fallback = _PROMPTS_DIR / "lake.txt"
    return fallback.read_text(encoding="utf-8") if fallback.exists() else ""


def apply_style(
    source_html: str,
    style_reference_html: str,
    theme_name: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    log_callback=None,
) -> tuple[Optional[str], Optional[dict]]:
    """Returns (styled_html, usage) where usage is
    {"input_tokens", "output_tokens", "cost_cad"} — or (None, None) on failure.
    """
    import anthropic

    def log(msg, level="info"):
        if log_callback:
            log_callback(msg, level)

    prompt_template = _load_prompt(theme_name)
    if not prompt_template:
        log(f"❌ No prompt file for theme '{theme_name}'", "error")
        return None, None

    cleaned_html = _clean_html(source_html)
    log(
        f"🧹 Cleaned HTML: {len(source_html):,} → {len(cleaned_html):,} chars"
        f"  ({len(cleaned_html.split()):,} words)",
        "info",
    )

    prompt = prompt_template.format(
        source_html=cleaned_html,
        style_reference_html=style_reference_html or "",
    )

    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            log(f"🤖 {model} — attempt {attempt}/{_MAX_RETRIES} (theme: {theme_name})", "info")
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )

            if response.stop_reason == "max_tokens":
                log(
                    f"❌ Response truncated at the {_MAX_TOKENS:,}-token output limit — "
                    "page is likely too large to restyle in one pass. Leaving existing "
                    "content untouched.",
                    "error",
                )
                return None, None

            result = next(b.text for b in response.content if b.type == "text").strip()

            if result.startswith("```"):
                lines = result.splitlines()
                start = 1 if lines[0].startswith("```") else 0
                end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                result = "\n".join(lines[start:end]).strip()

            result = _restore_kaltura_sizing(cleaned_html, result, log=log)

            if len(result) < len(cleaned_html) * 0.5:
                log(
                    f"❌ Styled result ({len(result):,} chars) is suspiciously short "
                    f"compared to the source ({len(cleaned_html):,} chars) — refusing to "
                    "overwrite existing content.",
                    "error",
                )
                return None, None

            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            usage["cost_cad"] = _cost_cad(model, usage["input_tokens"], usage["output_tokens"])

            log(f"✅ Done ({len(result):,} chars)", "success")
            log(
                f"🔢 Tokens: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out"
                f"  —  ${usage['cost_cad']:.4f} CAD",
                "info",
            )
            return result, usage

        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < _MAX_RETRIES:
                log(f"⚠ Server busy ({e.status_code}) — retrying in {_RETRY_DELAY}s...", "warning")
                time.sleep(_RETRY_DELAY)
            else:
                log(f"❌ Claude unavailable after {attempt} attempts: {e}", "error")
                return None, None

        except Exception as e:
            log(f"❌ Claude error: {e}", "error")
            return None, None
