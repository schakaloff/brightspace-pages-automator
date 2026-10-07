import re
import sys
import asyncio
from pathlib import Path
from typing import Optional
import anthropic

_COLOR_PROP_RE = re.compile(r'(?:^|(?<=;))\s*(?:color|background-color)\s*:[^;]*', re.IGNORECASE)


def _strip_color_from_style(style: str) -> str:
    cleaned = _COLOR_PROP_RE.sub('', style)
    # normalise leftover semicolons
    parts = [p.strip() for p in cleaned.split(';') if p.strip()]
    return '; '.join(parts)


def _clean_html(html: str) -> str:
    from bs4 import BeautifulSoup
    from icon_shortcodes import replace_fontawesome_shortcodes
    html = replace_fontawesome_shortcodes(html)
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
        is_bpa_icon = tag.name == "span" and bool(tag.get("data-bpa-icon"))
        attrs_to_remove = [
            a for a in list(tag.attrs)
            if (
                (a.startswith("data-") and not (is_bpa_icon and a == "data-bpa-icon"))
                or (a.startswith("aria-") and not (is_bpa_icon and a == "aria-label"))
                or a in noise_attrs
            )
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


def _visible_word_count(markup: str) -> int:
    """Words a reader would see, ignoring head, scripts and styles.

    Uses the lenient ``html.parser`` on purpose: it keeps content that follows
    a stray ``</html>``, which is exactly what the styler's own cleaning must be
    compared against.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(markup or "", "html.parser")
    for tag in soup.find_all(["head", "script", "style", "title"]):
        tag.decompose()
    return len(soup.get_text(" ", strip=True).split())


_PAGE_WIDTH_STYLE = (
    "width: 95% !important; max-width: 1000px !important; "
    "min-width: 0; box-sizing: border-box"
)


def _keep_page_container_wide(styled_html: str) -> str:
    """Keep a styled page wide when Brightspace renders it as a flex child.

    The visual editor puts page HTML in a normal block, while the student view
    applies the generated body's centered flex layout. If Claude omits a width
    from .main-container, the student view shrinks the card to its shortest
    content even though it fills the editor. An inline width survives the
    different wrappers Brightspace uses for those two views.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(styled_html, "html.parser")
    container = soup.select_one(".main-container")
    if container is None:
        return styled_html
    existing = str(container.get("style") or "").strip().rstrip(";")
    if _PAGE_WIDTH_STYLE in existing:
        return styled_html
    container["style"] = f"{existing}; {_PAGE_WIDTH_STYLE}" if existing else _PAGE_WIDTH_STYLE
    return str(soup)


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
# The model's real output ceiling is 128k; 64k is a generous working limit that
# still leaves room in a single pass. Values this large require the streaming
# API — a plain messages.create() would hit the SDK's HTTP timeout first.
_MAX_TOKENS = 64000
_MAX_RETRIES = 3
_RETRY_DELAY = 8  # seconds between retries on overload

# USD per 1M tokens (input, output) — from Anthropic's published pricing.
_PRICING_USD_PER_MTOK = {
    "claude-opus-5":    (5.00, 25.00),
    "claude-opus-4-5":  (5.00, 25.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
USD_TO_CAD = 1.38  # approximate — update if the exchange rate shifts meaningfully


def _cost_cad(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING_USD_PER_MTOK.get(model, _PRICING_USD_PER_MTOK[DEFAULT_MODEL])
    usd = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return usd * USD_TO_CAD


def _claude_error_detail(error: Exception) -> str:
    """Return useful, non-secret context even when an SDK exception is blank."""
    message = str(error).strip()
    status = getattr(error, "status_code", None)
    prefix = type(error).__name__
    if status is not None:
        prefix += f" (HTTP {status})"
    return f"{prefix}: {message or repr(error)}"


def _load_prompt(theme_name: str) -> str:
    path = _PROMPTS_DIR / f"{theme_name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    fallback = _PROMPTS_DIR / "lake.txt"
    return fallback.read_text(encoding="utf-8") if fallback.exists() else ""


async def apply_style(
    source_html: str,
    style_reference_html: str,
    theme_name: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    log_callback=None,
    _allow_refusal_fallback: bool = True,
) -> tuple[Optional[str], Optional[dict]]:
    """Returns (styled_html, usage) where usage is
    {"input_tokens", "output_tokens", "cost_cad"} — or (None, None) on failure.
    """


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
    source_words, cleaned_words = _visible_word_count(source_html), _visible_word_count(cleaned_html)
    if source_words >= 20 and cleaned_words < source_words * 0.6:
        log(
            f"❌ Cleaning kept only {cleaned_words:,} of the page's {source_words:,} words. "
            "Claude would restyle a page that is mostly missing, so styling was "
            "skipped and the page was left as it is.",
            "error",
        )
        return None, None

    prompt = prompt_template.format(
        source_html=cleaned_html,
        style_reference_html=style_reference_html or "",
    )
    prompt += (
        "\n\nICON CUES: Preserve every <span data-bpa-icon=\"...\"> element, its "
        "symbol, and its aria-label. Style each as a restrained, compact cue using "
        "the theme accent colour; keep it immediately beside the text it introduces. "
        "Do not replace these authored cues with a different library or print any "
        "[fa-*] shortcode. Do not add new decorative icons to resource rows. "
        "PAGE WIDTH: Give the outer .main-container width: 95% and max-width: 1000px. "
        "Brightspace may render it as a flex child; without an explicit width, "
        "a short file list collapses into a narrow box."
    )
    if 'bpa-topic-description' in cleaned_html:
        prompt += (
            "\n\nTOPIC DESCRIPTIONS: Keep each .bpa-topic-description directly beside "
            "the page, link, or file it describes. Preserve its authored text, "
            "formatting, and links. Keep the Description label readable and "
            "visually modest; do not move the description to another resource."
        )
    if "BPA: CLASSIC" in (style_reference_html or ""):
        prompt += (
            "\n\nCLASSIC PRESET DESIGN: Rebuild the source using the supplied classic "
            "card-and-gradient reference, even if the source was previously styled with "
            "another design. Use a .hero with a linear-gradient from --primary to --mid, "
            "a large white uppercase page title, and spacious rounded .action-card sections "
            "with shadows and theme-coloured section headings. Use ordinary underlined links "
            "in the theme primary colour. Do not retain calm resource-directory spacing, "
            "info-list panels, resource-row layouts, or action-link buttons from a previous "
            "design; preserve their authored content and destinations in the classic cards. "
            "Use only source content for titles and labels; reference placeholders are not content."
        )
    else:
        prompt += (
            "\n\nCALM PRESET READABILITY OVERRIDE: Rebuild the source using the supplied "
            "calm reference, even if the source was previously styled with another design. "
            "Use a calm hierarchy: headings and body copy are charcoal; reserve the primary "
            "colour for restrained accents; set --primary-soft to a low-opacity version of "
            "the primary colour and use it only for grouped information. Links use an accessible "
            "dark orange action colour and stay underlined. Do not move files to the bottom of "
            "the page. Only make an existing standalone Zoom/meeting link into an .action-link "
            "button when its nearby label makes the destination clear; preserve the original "
            "link URL and text in a visually-hidden .sr-only sibling. For clear labelled fields "
            "such as Professor, Email, or Office Location, use .info-list, .info-row, .info-label, "
            "and .info-value. Remove previous gradient banners and oversized uppercase title "
            "styling. Follow the supplied reference's quieter "
            "academic proportions. Do not add icons to labelled information rows and do not use "
            "vertical accent bars on the hero, information groups, or schedule groups. Do not use "
            "the theme colour for borders or outlines. Use spacing, pale backgrounds, and neutral "
            "gray rules for separation. Keep labels in normal case, at a readable size, with no "
            "wide letter spacing. "
            "Use comfortable body text rather than microcopy. An .action-link is a restrained "
            "soft-background action, never a filled promotional call-to-action. On a page that is "
            "mostly files and links, keep the first section close to the page title: no large empty "
            "gap, decorative short line under section headings, nested card, or oversized section "
            "padding. Use readable link titles and compact rows with subtle neutral separators. "
            "Give the outer .main-container a resource-directory class only when the page is mainly "
            "a files-and-links directory; normal reading or course-information pages keep their "
            "regular spacing.\n\n"
            "RESOURCE DIRECTORY OVERRIDE: For an unlabeled, mixed resource list, separate ordinary "
            "web/course links from downloadable files. Put ordinary links in one Links section using "
            ".resource-section, .link-list, and .link-row. Put downloadable files in one Files section "
            "using .resource-row and .resource-link. Preserve source order within each group, and do "
            "not duplicate a resource. When the source already has meaningful headings such as "
            "Lecture Slides, Lecture Recordings, or Course Materials, keep each resource directly "
            "under its heading in the original order; never leave that heading empty while moving "
            "its links to a generic Links or Files section. Treat a link as a file when its URL "
            "or label identifies PDF, XLS/XLSX, DOC/DOCX, PPT/PPTX, ZIP, or another downloadable "
            "course file, including /content/enforced/ URLs. In an unlabeled directory, keep "
            "internal /d2l/ topic links in Links. "
            "Do not invent either section when that resource type is absent.\n\n"
            "ACCESSIBLE STRUCTURE: Use exactly one <main class=\"content-body\"> for the page "
            "content. Keep heading levels in order: h1 for the page title, h2 for major sections, "
            "and h3 for subsections such as book titles. Do not jump from h2 to h4. Do not leave "
            "an empty Lecture Slides section when slide topic links are present.\n\n"
            "PLAIN RESOURCE ROWS: Each resource is one simple paragraph with a readable underlined "
            "text link. Do not add PDF/LINK/PAGE badges, icon fonts, SVGs, emoji, pseudo-element "
            "icons, second Open/Download actions, or a repeated file type when the title already "
            "makes it clear. Add metadata only when it gives the learner useful context.\n\n"
            "BRIGHTSPACE AUTHORING: Generated resources must remain easy to edit and mouse-copy in "
            "Brightspace's visual editor. A .link-row and .resource-row is a simple p container, "
            "never an anchor. Do not use CSS Grid, Flexbox, tables, fixed columns, or nested layout "
            "wrappers for resource rows; copied inner content must remain readable even if the editor "
            "does not include the outer p element. Give each resource exactly one shallow text-only anchor: "
            "a.link-title for a web/course link or a.resource-link for a file. That anchor may contain "
            "only its visible human-readable title, with no nested spans, icons, metadata, or action "
            "text. Put optional .link-meta or .resource-meta in a sibling span after the anchor. Do "
            "not create a second Open or Download link, trailing action column, button, arrow, or "
            "duplicate href. Avoid generated text in CSS pseudo-elements. If a raw source URL is "
            "replaced visually by a readable title, preserve its complete original text in a separate "
            ".sr-only sibling outside the anchor. Every visible icon and text node must exist in the "
            "HTML rather than a CSS pseudo-element. Keep the markup shallow so a non-technical "
            "instructor can select the title, use Edit Link, or mouse-copy and paste the row without "
            "collapsing its width or corrupting it."
        )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    content_retry_used = False
    usage = {"input_tokens": 0, "output_tokens": 0}

    # One content-repair request is independent of the network retry budget.
    for attempt in range(1, _MAX_RETRIES + 2):
        try:
            purpose = "; content repair" if content_retry_used else ""
            log(f"🤖 {model} — attempt {attempt} (theme: {theme_name}{purpose})", "info")
            async with client.messages.stream(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = await stream.get_final_message()

            if response.stop_reason == "max_tokens":
                log(
                    f"❌ Response truncated at the {_MAX_TOKENS:,}-token output limit — "
                    "page is likely too large to restyle in one pass. Leaving existing "
                    "content untouched.",
                    "error",
                )
                return None, None

            if getattr(response, "stop_reason", None) == "refusal":
                stop_details = getattr(response, "stop_details", None)
                category = getattr(stop_details, "category", None)
                if category is None and isinstance(stop_details, dict):
                    category = stop_details.get("category")
                detail = f" (category: {category})" if category else ""

                # A refusal is a completed response, rather than a transient
                # transport failure, so retrying the same model would only
                # repeat it. Opus can be more conservative for ordinary
                # formatting requests; give a non-default choice one safe
                # retry with the app default.
                if _allow_refusal_fallback and model != DEFAULT_MODEL:
                    log(
                        f"⚠ {model} refused this formatting request{detail}. "
                        f"Retrying once with {DEFAULT_MODEL}.",
                        "warning",
                    )
                    return await apply_style(
                        source_html=source_html,
                        style_reference_html=style_reference_html,
                        theme_name=theme_name,
                        api_key=api_key,
                        model=DEFAULT_MODEL,
                        log_callback=log_callback,
                        _allow_refusal_fallback=False,
                    )

                log(
                    f"❌ {model} refused this formatting request{detail}. "
                    "Leaving existing content untouched.",
                    "error",
                )
                return None, None

            text_blocks = [
                str(block.text).strip()
                for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            ]
            if not text_blocks:
                block_types = ", ".join(
                    str(getattr(block, "type", type(block).__name__))
                    for block in response.content
                ) or "none"
                log(
                    "❌ Claude returned no HTML text "
                    f"(stop reason: {getattr(response, 'stop_reason', 'unknown')}; "
                    f"blocks: {block_types}). Leaving existing content untouched.",
                    "error",
                )
                return None, None
            result = "\n".join(text_blocks)

            if result.startswith("```"):
                lines = result.splitlines()
                start = 1 if lines[0].startswith("```") else 0
                end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                result = "\n".join(lines[start:end]).strip()

            if not result:
                log("❌ Claude returned empty HTML. Leaving existing content untouched.", "error")
                return None, None

            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens

            result = _restore_kaltura_sizing(cleaned_html, result, log=log)
            result = _keep_page_container_wide(result)
            # The API can vary its link target from page to page. Keep the
            # authoring behavior deterministic, including Brightspace's
            # "New window" setting in the visual Edit Link dialog.
            from link_behavior import open_page_links_in_new_window
            result = open_page_links_in_new_window(result)

            # Imported pages can be mostly presentation markup. A shorter
            # rewrite is valid when its authored content is still present.
            # Always validate the repair response, even if it becomes longer.
            if content_retry_used or len(result) < len(cleaned_html) * 0.5:
                from content_preservation import content_is_preserved

                preserved, reason = content_is_preserved(
                    cleaned_html, result, allow_label_colons=True
                )
                if not preserved:
                    if not content_retry_used:
                        log(
                            f"⚠ Styled result is missing content ({reason}). "
                            "Retrying once from the original page.", "warning",
                        )
                        content_retry_used = True
                        prompt += (
                            "\n\nCONTENT REPAIR: Your previous result was incomplete: "
                            f"{reason}. Regenerate the complete styled page from SOURCE HTML. "
                            "Keep every authored passage, file link, image and media destination. "
                            "Formatting and wrapper markup may be shorter. Return the complete HTML."
                        )
                        continue
                    log(
                        f"❌ Styled result still has missing content ({reason}). "
                        "Leaving existing content untouched.", "error",
                    )
                    return None, None
                log(f"✓ Styled content verified ({len(cleaned_html):,} → {len(result):,} chars).", "info")

            usage["cost_cad"] = _cost_cad(model, usage["input_tokens"], usage["output_tokens"])

            log(f"✅ Done ({len(result):,} chars)", "success")
            log(
                f"🔢 Tokens: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out"
                f"  —  ${usage['cost_cad']:.4f} CAD",
                "info",
            )
            return result, usage

        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt - int(content_retry_used) < _MAX_RETRIES:
                log(f"⚠ Server busy ({e.status_code}) — retrying in {_RETRY_DELAY}s...", "warning")
                await asyncio.sleep(_RETRY_DELAY)
            else:
                log(
                    f"❌ Claude unavailable after {attempt} attempts: "
                    f"{_claude_error_detail(e)}",
                    "error",
                )
                return None, None

        # Network-level failure (DNS, TLS, dropped socket, timeout). No HTTP
        # status ever comes back, so this is NOT an APIStatusError and never
        # reached the retry above — a single blip used to abandon the whole
        # page. Covers APITimeoutError too, which subclasses this.
        except anthropic.APIConnectionError as e:
            if attempt - int(content_retry_used) < _MAX_RETRIES:
                log(f"⚠ Connection error — retrying in {_RETRY_DELAY}s...", "warning")
                await asyncio.sleep(_RETRY_DELAY)
            else:
                log(
                    f"❌ Could not reach Claude after {attempt} attempts: "
                    f"{_claude_error_detail(e)}",
                    "error",
                )
                return None, None

        except Exception as e:
            log(f"❌ Claude error: {_claude_error_detail(e)}", "error")
            return None, None

    # Only reachable if every attempt retried without ever returning.
    return None, None
