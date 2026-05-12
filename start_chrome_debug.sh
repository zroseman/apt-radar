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

if [ ! -e "$CHROME_APP" ]; then
    echo "ERROR: Google Chrome not found at $CHROME_APP"
    echo "Install Chrome from https://www.google.com/chrome/ and try again."
    exit 1
fi

# Note: we DON'T need to quit the user's existing Chrome. The --user-data-dir
# flag makes this a fully separate Chrome instance with its own profile —
# it runs alongside their daily Chrome without affecting it.

mkdir -p "$PROFILE_DIR"

echo "Launching debug Chrome (profile: $PROFILE_DIR)..."
# Open to about:blank — the wizard navigates to Yad2 (and Facebook if enabled)
# in dedicated tabs afterward. Yad2-only users shouldn't get a Facebook tab
# they don't need.
"$CHROME_APP" \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE_DIR" \
    about:blank &

# Give Chrome a moment to bind the port
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -s -m 2 "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
        echo ""
        echo "Debug Chrome ready at http://127.0.0.1:$PORT"
        exit 0
    fi
done

echo "Chrome launched, but the debug port is not responding after 10 seconds."
echo "If your daily Chrome was already running, it may have absorbed this launch."
echo "Try: quit Chrome completely (Cmd+Q) and re-run this script."
exit 1
