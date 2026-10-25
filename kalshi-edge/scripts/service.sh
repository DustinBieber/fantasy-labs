#!/usr/bin/env bash
# Run Kalshi Edge in the background on a Mac: starts at login, restarts if it crashes, and keeps the Mac
# awake while it runs (caffeinate). Called by ./start.sh install|restart|status|logs|uninstall.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
LABEL="com.kalshiedge.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$DIR/data/logs"
DOMAIN="gui/$(id -u)"

if [ "$(uname)" != "Darwin" ]; then
  echo "The background service is for macOS. On other systems, run ./start.sh in a terminal or tmux."
  exit 1
fi

case "${1:-}" in
  install)
    case "$DIR" in
      "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
        echo "macOS blocks background services from reading Desktop, Documents and Downloads."
        echo "Move this folder to your home folder first, for example:"
        echo "  mv \"$DIR\" ~/kalshi-edge && cd ~/kalshi-edge && ./start.sh install"
        exit 1
        ;;
    esac
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
    # First run installs packages in the foreground so any error shows here, not in a log file.
    ./start.sh test >/dev/null || { echo "Tests failed; fix that before installing the service (run ./start.sh test)."; exit 1; }
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-is</string>
    <string>/bin/bash</string><string>$DIR/start.sh</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/server.log</string>
</dict>
</plist>
EOF
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed. Kalshi Edge now runs in the background, starts when you log in, and restarts if it stops."
    echo "  Status:     ./start.sh status"
    echo "  After editing .env:  ./start.sh restart"
    echo "  Live log:   ./start.sh logs"
    echo "  Remove:     ./start.sh uninstall"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed the background service. Your data in data/ is untouched."
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$LABEL" | grep -E "state =|pid =|last exit code" || true
      PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
      code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT:-8000}/" || true)
      echo "Dashboard answers HTTP ${code:-none} (200 = up; 401 = up and asking for your APP_PASSWORD)."
    else
      echo "Not installed. Run ./start.sh install"
    fi
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Restarted (picks up .env changes)." \
      || echo "Not installed. Run ./start.sh install"
    ;;
  logs)
    exec tail -n 100 -f "$LOG_DIR/server.log"
    ;;
  *)
    echo "Usage: ./start.sh install|restart|status|logs|uninstall"; exit 1
    ;;
esac
