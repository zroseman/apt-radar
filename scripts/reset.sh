#!/bin/bash
# Wipe local state so the wizard can re-run from a clean slate.
#
# Removes:
#   .env, settings.json   — config (forces wizard to re-collect)
#   seen_posts.db          — the dedup + listings database
#   .last_scan.json        — last scan summary
#   .scan.pid              — stale scan PID file
#   monitor.log, dashboard_*.log, launchd_*.log — log clutter
#
# Keeps:
#   .venv/                 — Python deps (so you don't reinstall every time)
#   debug_chrome_profile/  — debug Chrome login cookies (so you don't re-login)
#   the code itself
#
# Usage: ./scripts/reset.sh           (interactive confirm)
#        ./scripts/reset.sh --yes     (no prompt — for scripted use)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ "$1" != "--yes" ]; then
    echo "About to wipe local state in: $SCRIPT_DIR"
    echo ""
    echo "Will delete: .env  settings.json  seen_posts.db  .last_scan.json  .scan.pid  *.log"
    echo "Will keep:   .venv/  debug_chrome_profile/  source code"
    echo ""
    read -p "Proceed? [y/N] " ans
    if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# Stop the dashboard if it's running (otherwise Flask will re-create
# settings.json from in-memory state)
pkill -f "python.*app.py" 2>/dev/null || true

rm -f .env settings.json seen_posts.db .last_scan.json .scan.pid
rm -f monitor.log dashboard_stdout.log dashboard_stderr.log launchd_stdout.log launchd_stderr.log

echo "Done. Run the wizard again — open Claude Code here and say 'set this up'."
