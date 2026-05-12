#!/bin/bash
# Installs Python dependencies into a virtualenv at ./.venv/
# Run once after cloning. Idempotent — safe to re-run.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Require Python 3.11+ (code uses `int | None` PEP 604 syntax, type hints)
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    echo "ERROR: Python 3.11 or newer is required."
    echo "  Detected: $(python3 --version 2>&1)"
    echo "  Install from https://www.python.org/downloads/ and re-run."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtualenv at .venv/ ..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
./.venv/bin/pip install --upgrade pip > /dev/null
./.venv/bin/pip install -r requirements.txt

# Smoke test — fail fast if any dep didn't install cleanly.
if ! ./.venv/bin/python3 -c "import anthropic, openai, flask, requests, websocket, certifi" 2>/dev/null; then
    echo "ERROR: dependency import smoke test failed. Re-run setup.sh or check pip output."
    exit 1
fi

echo ""
echo "Done. Claude Code will handle the next step when you ask 'set this up'."
