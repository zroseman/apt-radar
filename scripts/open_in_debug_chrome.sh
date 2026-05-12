#!/bin/bash
# Open a URL in the debug Chrome instance (port 9222), in a new tab.
# Usage: ./scripts/open_in_debug_chrome.sh <url>
set -e

URL="$1"
if [ -z "$URL" ]; then
    echo "Usage: $0 <url>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Prefer the venv Python if setup.sh was run; fall back to system python3.
if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi

"$PYTHON" <<PYEOF
import json
import sys
import urllib.request

URL = ${URL@Q}

try:
    info = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5).read())
except Exception as e:
    sys.exit(f"Debug Chrome not reachable on port 9222 — run ./start_chrome_debug.sh first. ({e})")

ws_url = info.get("webSocketDebuggerUrl")
if not ws_url:
    sys.exit("Chrome /json/version response missing webSocketDebuggerUrl")

# Use websocket-client (already in requirements.txt) to call Target.createTarget.
try:
    from websocket import create_connection
except ImportError:
    sys.exit("websocket-client not installed — run bash setup.sh first.")

ws = create_connection(ws_url, timeout=10)
try:
    ws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": URL}}))
    # Drain messages until we see our id=1 response (CDP may interleave events).
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        ws.settimeout(max(0.5, deadline - time.time()))
        raw = ws.recv()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("id") == 1:
            if "error" in data:
                sys.exit(f"CDP error: {data['error']}")
            print(f"Opened: {URL}")
            break
    else:
        sys.exit("Timed out waiting for CDP confirmation")
finally:
    ws.close()
PYEOF
