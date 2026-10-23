#!/bin/bash
# Mac mini: run Ops Hub in the background, start at login and restart if it stops.
#   ./run/install-mac-service.sh            install / update
#   ./run/install-mac-service.sh uninstall  remove
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(pwd)
LABEL=com.opshub.server
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Removed."; exit 0
fi
PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
[ -x .venv/bin/python ] || { "$PY" -m venv .venv && .venv/bin/pip install --quiet -e .; }
[ -f .env ] || cp .env.example .env
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>opshub</string><string>serve</string>
    <string>--host</string><string>${OPSHUB_HOST:-127.0.0.1}</string><string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/opshub.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/opshub.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Ops Hub is running in the background: http://localhost:8000   (logs: $DIR/logs/opshub.log)"
echo "Also set the Mac mini to never sleep: System Settings > Energy > Prevent automatic sleeping."
