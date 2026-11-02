"""Optional, best-effort visuals for the standalone H5P browser context.

The guide never clicks, types, reads field values, or changes transfer decisions.
Every browser operation is bounded; cancellation still propagates normally.
"""
import asyncio


GUIDE_SCRIPT = r"""(() => {
    if (window.__h5pGuide) return;
    const channel = '__h5p_guide_point_v1';
    let state = {message: 'Connecting your courses…', activity: '', kind: 'working'};
    let root, cursor, bubble, heading, detail, counter, motion, fade;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const isTop = window === window.top;

    function mount() {
        if (!isTop || root || !document.documentElement) return;
        const host = document.createElement('div');
        host.id = '__h5p_browser_guide';
        // Keep this outside editable page content; no controls to confuse automation.
        host.style.cssText = 'all:initial!important;position:fixed!important;inset:0!important;'+
            'width:100vw!important;height:100vh!important;margin:0!important;padding:0!important;'+
            'border:0!important;background:transparent!important;pointer-events:none!important;'+
            'z-index:2147483647!important;overflow:visible!important;';
        host.setAttribute('aria-hidden', 'true');
        document.documentElement.appendChild(host);
        // A manual popover keeps the guide visible above native <dialog> windows.
        try { host.setAttribute('popover', 'manual'); host.showPopover(); } catch (_) {}
        root = host.attachShadow({mode: 'open'});
        root.innerHTML = `<style>
            :host, :host * {pointer-events:none!important;box-sizing:border-box}
            .card {position:fixed;left:20px;bottom:20px;width:min(380px,calc(100vw - 40px));
                padding:15px 18px;border:1px solid #ffffff30;border-radius:16px;
                background:#16253af2;color:#f7faff;box-shadow:0 8px 30px #10203030;
                font:14px/1.45 system-ui,sans-serif;overflow-wrap:anywhere}
            .brand {color:#a8c7ef;font-size:10px;font-weight:750;letter-spacing:1.4px;margin-bottom:6px}
            .message {font-weight:650;font-size:15px}.detail {color:#c8d8ec;margin-top:4px}
            .count {color:#9ee6d0;font-size:12px;margin-top:8px}
            .card[data-kind="attention"] {border-color:#f2b85b}.card[data-kind="success"] {border-color:#70d6b1}
            .cursor {position:fixed;left:0;top:0;width:20px;height:28px;opacity:1;
                transform:translate(56px,56px);filter:drop-shadow(0 1px 1px #07132230)}
            .cursor svg {position:absolute;overflow:visible}
            .cursor-arrow {left:-1.2px;top:-1.2px}
            .cursor-hand {display:none;left:-9.5px;top:-1.8px}
            .cursor[data-kind="hand"] .cursor-arrow {display:none}
            .cursor[data-kind="hand"] .cursor-hand {display:block}
            .bubble {position:fixed;max-width:min(270px,calc(100vw - 24px));padding:7px 11px;
                border-radius:10px;background:#16253af5;color:white;box-shadow:0 3px 14px #0002;
                font:12px/1.4 system-ui,sans-serif;overflow-wrap:anywhere;opacity:0}
            .pulse {position:fixed;width:26px;height:26px;border:1px solid #6b8399;
                border-radius:50%;margin:-13px 0 0 -13px}
        </style><div class="card"><div class="brand">H5P TRANSFER · LIVE</div>
            <div class="message"></div><div class="detail"></div><div class="count"></div></div>
            <div class="cursor" data-kind="arrow">
                <svg class="cursor-arrow" width="20" height="28" viewBox="0 0 24 34">
                    <path d="M1.5 1.5L22.5 26L10 24.5L1.5 32Z" fill="#03071f"/>
                    <path d="M1.5 1.5L10 24.5L1.5 32Z" fill="#102c43"/>
                    <path d="M1.5 1.5L22.5 26L10 24.5L1.5 32Z" fill="none"
                        stroke="#dff1f3" stroke-width="1.6" stroke-linejoin="round"/>
                </svg>
                <svg class="cursor-hand" width="24" height="30" viewBox="0 0 28 35">
                    <path d="M8 18V5C8 1 14 1 14 5V13.5C15.5 12 18 12.5 18.7 14.5
                        C20.4 13.3 22.8 14 23.3 16C25.3 15.4 27 17 27 19V25
                        C27 30 23 34 18 34H16C12 34 9.5 32 7 29L1.5 22.5
                        C-1 19.5 2.5 16 5 18.5L8 21Z" fill="#dff1f3"/>
                    <path d="M20 14C21.8 21.5 20.4 29 15 30C10.8 30.8 8.5 28.5 6 27.5
                        L7 29C9.5 32 12 34 16 34H18C23 34 27 30 27 25V19
                        C27 17 25.3 15.4 23.3 16C22.8 14.5 21.5 13.8 20 14Z" fill="#c2d3d8"/>
                    <path d="M8 18V5C8 1 14 1 14 5V13.5C15.5 12 18 12.5 18.7 14.5
                        C20.4 13.3 22.8 14 23.3 16C25.3 15.4 27 17 27 19V25
                        C27 30 23 34 18 34H16C12 34 9.5 32 7 29L1.5 22.5
                        C-1 19.5 2.5 16 5 18.5L8 21Z" fill="none" stroke="#03071f"
                        stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
                </svg>
            </div>
            <div class="bubble"></div>`;
        cursor = root.querySelector('.cursor'); bubble = root.querySelector('.bubble');
        heading = root.querySelector('.message'); detail = root.querySelector('.detail');
        counter = root.querySelector('.count');
        render();
    }
    function render() {
        if (!root) return;
        heading.textContent = state.message;
        detail.textContent = state.activity || '';
        counter.textContent = state.total ? `Activity ${state.current} of ${state.total}` : '';
        root.querySelector('.card').dataset.kind = state.kind;
    }
    function update(next) { state = {...state, ...next}; mount(); render(); }
    function cursorKind(target) {
        if (target.matches(':disabled,[aria-disabled="true"]')) return 'arrow';
        return target.matches('button,a[href],label,select,d2l-button,d2l-button-icon,d2l-menu-item,'+
            '[role="button"],[role="menuitem"],[role="tab"],[role="checkbox"],'+
            'input:is([type="button"],[type="submit"],[type="reset"],[type="checkbox"],[type="radio"],[type="file"])') ||
            getComputedStyle(target).cursor === 'pointer' ? 'hand' : 'arrow';
    }
    async function point(p) {
        mount();
        if (!root || !Number.isFinite(p.x) || !Number.isFinite(p.y) ||
            p.x < 0 || p.y < 0 || p.x > innerWidth || p.y > innerHeight) return;
        const x = p.x, y = p.y;
        const card = root.querySelector('.card');
        const bounds = card.getBoundingClientRect();
        if (x >= bounds.left-20 && x <= bounds.right+20 && y >= bounds.top-20 && y <= bounds.bottom+20) {
            const atBottom = card.style.top !== '20px';
            card.style.top = atBottom ? '20px' : 'auto';
            card.style.bottom = atBottom ? 'auto' : '20px';
        }
        const previous = cursor.getBoundingClientRect();
        if (motion) motion.cancel();
        cursor.style.opacity = '1';
        const end = `translate(${x}px,${y}px)`;
        const distance = Math.hypot(x - previous.x, y - previous.y);
        const duration = reduced || distance < 5 ? 0 : Math.min(300, 140 + distance / 5);
        // Travel as an arrow; the hand's fingertip becomes the hotspot on arrival.
        if (duration) cursor.dataset.kind = 'arrow';
        cursor.style.transform = end;
        motion = cursor.animate([
            {transform:`translate(${previous.x}px,${previous.y}px)`},
            {transform:`translate(${(previous.x+x)/2+Math.min(22,distance/12)}px,${(previous.y+y)/2-12}px)`,offset:0.55},
            {transform:end}
        ], {duration, easing:'cubic-bezier(.25,.1,.25,1)'});
        bubble.textContent = p.label || 'Clicking control';
        bubble.style.opacity = '1';
        bubble.style.left = `${Math.max(12, Math.min(x+28, innerWidth-290))}px`;
        bubble.style.top = `${Math.max(12, Math.min(y+30, innerHeight-80))}px`;
        clearTimeout(fade);
        // Only the transient action label fades. Keep the cursor visible through
        // API checks, downloads and other waits that do not produce DOM clicks.
        fade = setTimeout(() => { bubble.style.opacity = '0'; }, 2400);
        try { await motion.finished; } catch (_) { return; }
        cursor.dataset.kind = p.kind === 'hand' ? 'hand' : 'arrow';
        if (p.click) {
            const pulse = document.createElement('div'); pulse.className = 'pulse';
            pulse.style.left = `${x}px`; pulse.style.top = `${y}px`; root.appendChild(pulse);
            const animation = pulse.animate([{transform:'scale(.6)',opacity:0.65},
                {transform:`scale(${reduced ? 1 : 1.45})`,opacity:0}], {duration:400});
            animation.finished.then(() => pulse.remove()).catch(() => pulse.remove());
        }
    }
    function send(p) {
        if (isTop) { void point(p); return; }
        parent.postMessage({channel, point:p}, '*');
    }
    // Each parent translates only messages from one of its actual child frames.
    // This also works for cross-origin H5P frames and frames in open shadow roots.
    function childFrame(source, scope=document) {
        for (const el of scope.querySelectorAll('*')) {
            if ((el.tagName === 'IFRAME' || el.tagName === 'FRAME') && el.contentWindow === source) return el;
            if (el.shadowRoot) { const found = childFrame(source, el.shadowRoot); if (found) return found; }
        }
    }
    window.addEventListener('message', event => {
        if (!event.data || event.data.channel !== channel) return;
        const p = event.data.point;
        if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.y)) return;
        const frame = childFrame(event.source); if (!frame) return;
        const r = frame.getBoundingClientRect();
        const sx = r.width / (frame.offsetWidth || r.width || 1);
        const sy = r.height / (frame.offsetHeight || r.height || 1);
        send({x:r.left+(p.x+frame.clientLeft)*sx, y:r.top+(p.y+frame.clientTop)*sy,
            label:String(p.label || '').slice(0,90), click:!!p.click, kind:p.kind});
    });
    function action(event) {
        const path = event.composedPath();
        const target = path.find(el => el.matches && el.matches(
            'button,a,label,input,textarea,select,d2l-button,d2l-button-icon,d2l-menu-item,'+
            '[role="button"],[role="menuitem"],[role="tab"],[role="checkbox"],[contenteditable="true"]'));
        if (!target) return;
        const r = target.getBoundingClientRect();
        if (!r.width || !r.height) return;
        // Labels only: never field values, typed text, URLs, or credentials.
        const label = target.getAttribute('aria-label') || target.getAttribute('title') ||
            ((target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
                ? 'field' : (target.textContent || '').trim().replace(/\s+/g,' ')) || 'control';
        const nativePoint = event.isTrusted && event.type === 'click';
        send({x:nativePoint ? event.clientX : r.left+r.width/2,
            y:nativePoint ? event.clientY : r.top+r.height/2,
            label:(event.type === 'click' ? 'Clicking ' : 'Editing ') + label.slice(0,70),
            click:event.type === 'click', kind:cursorKind(target)});
    }
    document.addEventListener('click', action, true);
    document.addEventListener('focusin', event => {
        if (event.target.matches && event.target.matches('input,textarea,[contenteditable="true"]')) action(event);
    }, true);
    window.__h5pGuide = {update, point, cursorKind};
    mount();
    document.addEventListener('DOMContentLoaded', mount, {once:true});
    if (isTop && window.__h5pGuideState) window.__h5pGuideState().then(update).catch(() => {});
})();"""


class H5PBrowserGuide:
    def __init__(self, on_state=None, enabled=True):
        self.on_state = on_state
        self.enabled = enabled
        self.context = None
        self.state = dict(message='Connecting your courses…', activity='', current=0, total=0, kind='working')

    async def install(self, context):
        if not self.enabled:
            return
        try:
            await asyncio.wait_for(context.expose_binding('__h5pGuideState', lambda source: dict(self.state)), 1)
            await asyncio.wait_for(context.add_init_script(script=GUIDE_SCRIPT), 1)
            self.context = context
            await asyncio.gather(*(self._evaluate(page, GUIDE_SCRIPT) for page in context.pages))
        except Exception:
            # Visuals must never stop a real transfer (including unsupported pages).
            self.context = None

    async def _evaluate(self, page, script, arg=None):
        try:
            return await asyncio.wait_for(page.evaluate(script, arg), 0.8)
        except Exception:
            return None

    async def status(self, message, *, activity='', current=0, total=0, kind='working'):
        self.state = dict(message=message, activity=activity, current=current, total=total, kind=kind)
        if self.on_state:
            try:
                self.on_state(dict(stage='watch', **self.state))
            except Exception:
                pass
        if self.context is not None:
            try:
                await asyncio.gather(*(self._evaluate(page, '(s) => window.__h5pGuide?.update(s)', self.state)
                                       for page in self.context.pages))
            except Exception:
                pass

    async def point(self, locator, label):
        if self.context is None:
            return
        try:
            box = await asyncio.wait_for(locator.bounding_box(timeout=300), 0.4)
            if box:
                kind = await asyncio.wait_for(locator.evaluate(
                    "(el) => window.__h5pGuide?.cursorKind(el) || 'arrow'", timeout=300), 0.4)
                await self._evaluate(locator.page, '(p) => window.__h5pGuide?.point(p)',
                    dict(x=box['x']+box['width']/2, y=box['y']+box['height']/2, label=label, kind=kind))
        except Exception:
            pass
