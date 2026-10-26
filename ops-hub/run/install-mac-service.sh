#!/bin/bash
# Mac mini: run Ops Hub in the background and restart it if it ever stops.
#
#   ./run/install-mac-service.sh              start when you log in (LaunchAgent)
#   sudo ./run/install-mac-service.sh --boot  start when the Mac boots, no login needed (LaunchDaemon)
#   ./run/install-mac-service.sh uninstall    remove (use sudo if you installed with --boot)
#
# Reachable from the MacBook Air / phone? Set OPSHUB_PASSWORD in .env, then install with
#   OPSHUB_HOST=0.0.0.0 ./run/install-mac-service.sh      (or: sudo OPSHUB_HOST=0.0.0.0 ... --boot)
set -euo pipefail
# sudo and Finder start with a bare PATH; Homebrew lives here on Apple-silicon and Intel Macs.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
DIR=$(pwd)
LABEL=com.opshub.server
HOST="${OPSHUB_HOST:-127.0.0.1}"
RUN_AS="${SUDO_USER:-$(id -un)}"
AGENT="/Users/$RUN_AS/Library/LaunchAgents/$LABEL.plist"
DAEMON="/Library/LaunchDaemons/$LABEL.plist"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u "$RUN_AS")/$LABEL" 2>/dev/null || true
  rm -f "$AGENT"
  if [ "$(id -u)" = 0 ]; then launchctl bootout "system/$LABEL" 2>/dev/null || true; rm -f "$DAEMON"; fi
  echo "Removed."; exit 0
fi

as_user() { if [ "$(id -u)" = 0 ]; then sudo -u "$RUN_AS" "$@"; else "$@"; fi; }
PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || true)
[ -n "$PY" ] || { echo "Python 3.11+ not found. Install it first: brew install python@3.12"; exit 1; }
[ -x .venv/bin/python ] || { as_user "$PY" -m venv .venv && as_user .venv/bin/pip install --quiet -e .; }
[ -f .env ] || as_user cp .env.example .env
as_user mkdir -p logs backups
if [ "$HOST" != "127.0.0.1" ] && ! grep -Eq '^OPSHUB_PASSWORD=[^[:space:]#]' .env; then
  echo "Refusing to listen on $HOST without a password: set OPSHUB_PASSWORD=... in $DIR/.env first."; exit 1
fi

plist() {  # $1 = include UserName (daemon)
  cat <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  $( [ "$1" = yes ] && echo "<key>UserName</key><string>$RUN_AS</string>" )
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>opshub</string><string>serve</string>
    <string>--host</string><string>$HOST</string><string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/logs/opshub.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/opshub.log</string>
</dict></plist>
PL
}

if [ "${1:-}" = "--boot" ]; then
  [ "$(id -u)" = 0 ] || { echo "--boot needs sudo: sudo $0 --boot"; exit 1; }
  launchctl bootout "gui/$(id -u "$RUN_AS")/$LABEL" 2>/dev/null || true; rm -f "$AGENT"  # one copy only
  plist yes > "$DAEMON"; chown root:wheel "$DAEMON"; chmod 644 "$DAEMON"
  launchctl bootout "system/$LABEL" 2>/dev/null || true
  launchctl bootstrap system "$DAEMON"
  echo "Ops Hub starts whenever this Mac boots (listening on $HOST:8000). Logs: $DIR/logs/opshub.log"
else
  mkdir -p "$(dirname "$AGENT")"
  plist no > "$AGENT"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENT"
  echo "Ops Hub runs while $RUN_AS is logged in (listening on $HOST:8000). Logs: $DIR/logs/opshub.log"
fi
echo "Open http://localhost:8000 on this Mac. Also: System Settings > Energy > Prevent automatic sleeping."
