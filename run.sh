#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

# ── First-run setup ────────────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/python" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
    "$VENV/bin/playwright" install chromium
    echo "Setup complete."
fi

# ── Launch ─────────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
exec "$VENV/bin/python" gui.py "$@"
