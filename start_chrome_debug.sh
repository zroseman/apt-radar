#!/bin/bash
# Launches a dedicated Chrome instance with remote-debugging enabled,
# using a separate profile directory so it doesn't touch the user's daily Chrome.
# Required for the Facebook scraping path (FB blocks automated browsers; we
# drive a real, logged-in Chrome via the DevTools Protocol on port 9222).
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="$SCRIPT_DIR/debug_chrome_profile"
PORT=9222
CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# If a debug Chrome is already running on this port, do nothing.
if curl -s -m 2 "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
    echo "Debug Chrome is already running on port $PORT — nothing to do."
    exit 0
fi

# If any other Chrome is running, the debug flag will be ignored on a new launch.
if pgrep -x "Google Chrome" > /dev/null; then
    echo "ERROR: Chrome is already running, but not in debug mode."
    echo "Quit Chrome completely first (Cmd+Q from the Chrome menu — not just closing windows),"
    echo "then re-run this script."
    exit 1
fi

if [ ! -e "$CHROME_APP" ]; then
    echo "ERROR: Google Chrome not found at $CHROME_APP"
    echo "Install Chrome from https://www.google.com/chrome/ and try again."
    exit 1
fi

mkdir -p "$PROFILE_DIR"

echo "Launching debug Chrome (profile: $PROFILE_DIR)..."
"$CHROME_APP" \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE_DIR" \
    https://www.facebook.com/ &

# Give Chrome a moment to bind the port
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -s -m 2 "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
        echo ""
        echo "Debug Chrome ready at http://127.0.0.1:$PORT"
        echo "Log into Facebook in the new Chrome window if you haven't already."
        exit 0
    fi
done

echo "Chrome launched, but the debug port is not responding yet."
echo "Wait a few seconds, then check: curl http://127.0.0.1:$PORT/json/version"
