# Shared helpers used across multiple modules.

import html as _html


def _norm(text: str) -> str:
    """Lowercase + decode HTML entities so &amp; == & in comparisons."""
    return _html.unescape(text).lower().strip()


DEEP_FIND_JS = """
    function deepFind(root, fn, depth) {
        depth = depth === undefined ? 15 : depth;
        if (!root || depth <= 0) return null;
        var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (fn(el)) return el;
            if (el.shadowRoot) {
                var found = deepFind(el.shadowRoot, fn, depth - 1);
                if (found) return found;
            }
        }
        return null;
    }
    """
