#!/bin/bash
# Installs Python dependencies into a virtualenv at ./.venv/
# Run once after cloning. Idempotent — safe to re-run.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Creating virtualenv at .venv/ ..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
./.venv/bin/pip install --upgrade pip > /dev/null
./.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Next step: add ANTHROPIC_API_KEY to .env"
echo "  cp .env.example .env"
echo "  # then edit .env"
