#!/bin/bash
# Installs a launchd LaunchAgent that runs a daily Apt Radar scan at the
# specified time. Generates the plist with the user's home path + chosen
# hour/minute substituted.
#
# Usage: ./install_scan_launchd.sh <HOUR> <MINUTE>
#   e.g. ./install_scan_launchd.sh 10 0   # daily at 10:00 local
#
# Heads up: macOS laptops in clamshell sleep (lid closed) often miss the
# scheduled time. Either keep the lid open or pick a time when the laptop
# is reliably awake.
set -e

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <HOUR> <MINUTE>"
    echo "  e.g. $0 10 0   # daily at 10:00 local"
    exit 1
fi

HOUR="$1"
MIN="$2"

if ! [[ "$HOUR" =~ ^[0-9]+$ ]] || [ "$HOUR" -lt 0 ] || [ "$HOUR" -gt 23 ]; then
    echo "HOUR must be 0..23 (got: $HOUR)"
    exit 1
fi
if ! [[ "$MIN" =~ ^[0-9]+$ ]] || [ "$MIN" -lt 0 ] || [ "$MIN" -gt 59 ]; then
    echo "MINUTE must be 0..59 (got: $MIN)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.apt-radar.scan"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/run_monitor.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$HOUR</integer>
        <key>Minute</key>
        <integer>$MIN</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/launchd_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>TLV_APT_FOREGROUND</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

printf "Installed: %s\n" "$PLIST_PATH"
printf "Daily scan scheduled for %02d:%02d local.\n" "$HOUR" "$MIN"
echo ""
echo "If macOS doesn't fire it on time (lid-closed sleep is the usual cause), see:"
echo "  https://github.com/zroseman/apt-radar#scheduled-scans-troubleshooting"
