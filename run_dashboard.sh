#!/bin/bash
# Runs the Flask dashboard on port 5055.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi
cd "$SCRIPT_DIR"

# Prefer the venv Python if setup.sh was run; fall back to system python3.
if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi

exec "$PYTHON" "$SCRIPT_DIR/app.py"
