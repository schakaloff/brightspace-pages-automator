"""Create content units (modules) in a Brightspace course.

Standalone so any tab can create a unit without constructing a ContentChecker.
The click path is the same one ContentChecker._create_missing_units drives:
Lessons page -> "New Unit" -> "Create Unit" -> title editor -> Save.

Every control on that page lives inside nested shadow DOM, so each step goes
through deepFind and clicks the inner <button> directly — Playwright's own
click() does not reach through the shadow boundaries.
"""

from js_helpers import DEEP_FIND_JS, _norm


async def create_unit(tab, bs_base: str, course_id: str, name: str, log) -> bool:
    """Create one unit titled `name`. Returns True if it was saved.

    `tab` must be a Playwright Page already logged in to Brightspace.
    Never raises — failures are logged and reported as False so callers can
    carry on with the units that did work.
    """
    df = DEEP_FIND_JS
    try:
        await tab.goto(
            f"{bs_base}/d2l/le/lessons/{course_id}",
            wait_until="domcontentloaded", timeout=20000,
        )
        await tab.wait_for_timeout(2000)

        # 1) "New Unit" dropdown opener — rendered lazily, so poll for it
        opened = False
        for _ in range(15):
            opened = await tab.evaluate(f"""() => {{
                {df}
                var host = deepFind(document, function(e) {{
                    return (e.id || '') === 'generate-unit-btn';
                }});
                if (!host) return false;
                var sub = host.shadowRoot ? host.shadowRoot.querySelector('d2l-button-subtle') : null;
                var target = sub || host;
                var inner = target.shadowRoot ? target.shadowRoot.querySelector('button') : null;
                (inner || target).click();
                return true;
            }}""")
            if opened:
                break
            await tab.wait_for_timeout(1000)
        if not opened:
            log(f"  ✗ 'New Unit' button not found — cannot create {name!r}", "error")
            return False
        await tab.wait_for_timeout(800)

        # 2) "Create Unit" item inside the dropdown
        create_clicked = False
        for _ in range(5):
            create_clicked = await tab.evaluate(f"""() => {{
                {df}
                var item = deepFind(document, function(e) {{
                    return (e.id || '') === 'createUnit';
                }});
                if (!item) return false;
                var inner = item.shadowRoot ? item.shadowRoot.querySelector('div.d2l-menu-item-text') : null;
                (inner || item).click();
                return true;
            }}""")
            if create_clicked:
                break
            await tab.wait_for_timeout(1000)
        if not create_clicked:
            log(f"  ✗ 'Create Unit' menu item not found — cannot create {name!r}", "error")
            return False
        try:
            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await tab.wait_for_timeout(1500)

        # 3) Title editor. The real input replaces a d2l-skeletize placeholder
        # late on cold loads, so poll rather than querying once.
        title_filled = False
        for _ in range(15):
            for sel in ['input.d2l-input[maxlength="150"]', 'input.d2l-input']:
                try:
                    loc = tab.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(click_count=3)
                        await loc.fill(name)
                        title_filled = True
                        break
                except Exception:
                    pass
            if title_filled:
                break
            await tab.wait_for_timeout(1000)
        if not title_filled:
            log(f"  ✗ Unit title input not found for {name!r}", "error")
            return False
        await tab.wait_for_timeout(500)

        saved = await tab.evaluate(f"""() => {{
            {df}
            var btn = deepFind(document, function(e) {{
                return (e.tagName || '').toUpperCase() === 'D2L-BUTTON'
                    && e.classList && e.classList.contains('d2l-desktop');
            }});
            if (!btn) return false;
            var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
            (inner || btn).click();
            return true;
        }}""")
        if not saved:
            log(f"  ⚠ Save button not found for {name!r}", "warning")
            return False

        await tab.wait_for_timeout(2000)
        log(f"  ✓ Created unit: {name}", "success")
        return True

    except Exception as e:
        log(f"  ✗ Unit creation failed for {name!r}: {str(e).splitlines()[0]}", "error")
        return False


def resolve_new_module_names(requested: list, existing_modules: list) -> tuple:
    """Split requested unit names into (reuse, to_create).

    `reuse` maps a requested name to the id of an existing module with the same
    normalized title — creating a second module with an identical name would
    leave the user two indistinguishable destinations. `to_create` preserves
    request order with duplicates collapsed, so two sections asking for the same
    new name produce one unit that both point at.
    """
    by_norm = {_norm(m["title"]): m for m in existing_modules}
    reuse: dict = {}
    to_create: list = []
    for name in requested:
        clean = (name or "").strip()
        if not clean:
            continue
        match = by_norm.get(_norm(clean))
        if match:
            reuse[clean] = match["id"]
        elif clean not in to_create:
            to_create.append(clean)
    return reuse, to_create
