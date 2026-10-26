#!/bin/bash
# Kalshi Edge — one-command setup for the always-on Mac. Safe to re-run: it also updates an existing install
# and keeps your settings, data, and the AI trader's record.
#
#   bash kalshi-edge/mac/setup.sh        (from any download of this repo; add --smoke to re-check data sources)
#   ~/KalshiEdge/kalshi update           (after the first install: downloads the latest main and runs this)
#
# What it does:
#   1. Gets Python 3.10+ (uses yours, or installs a private copy with `uv`; no admin rights needed)
#   2. Copies the app to ~/KalshiEdge/app, installs its libraries, and runs the test suite before switching over
#   3. First run: creates ~/KalshiEdge/kalshi-edge.env (asks for a dashboard password and an optional Odds API key)
#   4. First run: checks every live data source once and prints the VERIFY list
#   5. Installs a LaunchAgent on port 8100 (Family Hub keeps 8000): starts at login, restarts if it crashes
#   6. Offers to turn the AI trader on in PAPER mode (never live: that stays a button you press yourself)
#   7. If Tailscale is installed, a private https address on port 9443 for your own devices. Never Funnel:
#      trading controls don't belong on the public internet, and Funnel can't use 9443 even by mistake.
#   8. Installs the helper ~/KalshiEdge/kalshi (status, restart, logs, smoke, stop, update, uninstall)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${KALSHI_EDGE_HOME:-$HOME/KalshiEdge}"
PORT="${KALSHI_EDGE_PORT:-8100}"
TS_PORT="${KALSHI_EDGE_TS_PORT:-9443}"
LABEL="com.kalshiedge.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$HOME_DIR/kalshi-edge.env"
VPY="$HOME_DIR/venv/bin/python"
RUN_SMOKE=0
[ "${1:-}" = "--smoke" ] && RUN_SMOKE=1

say()  { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!!  %s\033[0m\n" "$*"; }
ask()  { local a=""; { read -r -p "$1 [Y/n] " a </dev/tty; } 2>/dev/null || return 1; [ "$a" != "n" ] && [ "$a" != "N" ]; }
set_env() {  # set_env KEY VALUE: replace or append a line. Single quotes, so dotenv reads the value literally.
  local tmp; tmp="$(mktemp)"
  grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
  printf "%s='%s'\n" "$1" "$2" >> "$tmp"
  mv "$tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
}
http_code() { curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/" 2>/dev/null || true; }

[ "$(uname)" = "Darwin" ] || { echo "Run this on the Mac that will host Kalshi Edge."; exit 1; }
mkdir -p "$HOME_DIR/logs" "$HOME_DIR/data"

# ---------------------------------------------------------------- Python
say "Finding Python 3.10 or newer"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "macOS's built-in Python is too old; installing a private Python 3.12 with uv (free, no admin rights)."
  if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
  "$UV" python install 3.12
  PY="$("$UV" python find 3.12)"
fi
echo "Using $PY ($("$PY" --version))"

# ---------------------------------------------------------------- app + libraries + tests
say "Installing Kalshi Edge into $HOME_DIR"
# A copy outside Desktop/Documents/Downloads avoids macOS privacy blocks for background processes, and keeps
# the running app independent of whatever folder it was installed from.
rm -rf "$HOME_DIR/app.new" && mkdir -p "$HOME_DIR/app.new"
cp -R "$SRC_DIR/app" "$SRC_DIR/static" "$SRC_DIR/scripts" "$SRC_DIR/tests" "$SRC_DIR/requirements.txt" "$HOME_DIR/app.new/"
[ -x "$VPY" ] || "$PY" -m venv "$HOME_DIR/venv"
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r "$HOME_DIR/app.new/requirements.txt"
echo "Running the test suite (about 10 seconds)…"
if ! (cd "$HOME_DIR/app.new" && env -u KALSHI_EDGE_ENV_FILE "$VPY" -m pytest -q -p no:cacheprovider >"$HOME_DIR/logs/tests.log" 2>&1); then
  tail -n 25 "$HOME_DIR/logs/tests.log"
  rm -rf "$HOME_DIR/app.new"
  warn "Tests failed, so nothing was changed. The previous version (if any) is still running."
  exit 1
fi
tail -n 1 "$HOME_DIR/logs/tests.log"
rm -rf "$HOME_DIR/app.old"
[ -d "$HOME_DIR/app" ] && mv "$HOME_DIR/app" "$HOME_DIR/app.old"
mv "$HOME_DIR/app.new" "$HOME_DIR/app"

# ---------------------------------------------------------------- settings
FIRST_RUN=0
if [ ! -f "$ENV_FILE" ]; then
  FIRST_RUN=1; RUN_SMOKE=1
  say "Creating $ENV_FILE"
  : > "$ENV_FILE"; chmod 600 "$ENV_FILE"
  PW=""
  while :; do
    { read -r -s -p "Choose a dashboard password (12+ characters; press Enter to have one made for you): " PW </dev/tty; } 2>/dev/null || PW=""
    echo
    if [ -z "$PW" ]; then
      PW="$("$VPY" -c 'import secrets; print(secrets.token_urlsafe(15))')"
      echo "Your dashboard password: $PW"
      echo "Save it in your password manager. Your browser can remember it too."
      break
    fi
    case "$PW" in
      *"'"*|*'"'*|*'\'*|*' '*) echo "Please avoid quotes, backslashes, and spaces." ;;
      *) [ "${#PW}" -ge 12 ] && break || echo "Use at least 12 characters." ;;
    esac
  done
  ODDS=""
  { read -r -p "The Odds API key (free at the-odds-api.com; needed to price spreads, totals and props). Enter to skip: " ODDS </dev/tty; } 2>/dev/null || ODDS=""
  set_env APP_PASSWORD "$PW"
  set_env ODDS_API_KEY "$(printf '%s' "$ODDS" | tr -cd 'A-Za-z0-9')"
fi
# Settings every version needs (re-runs only add what's missing; your values are kept).
grep -q '^APP_HOST=' "$ENV_FILE" || set_env APP_HOST "0.0.0.0"
grep -q '^APP_PORT=' "$ENV_FILE" || set_env APP_PORT "$PORT"
grep -q '^DATABASE_URL=' "$ENV_FILE" || set_env DATABASE_URL "sqlite:///$HOME_DIR/data/kalshi_edge.db"
for k in ODDS_API_KEY KALSHI_API_KEY_ID KALSHI_PRIVATE_KEY_PATH TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  grep -q "^$k=" "$ENV_FILE" || set_env "$k" ""
done

# ---------------------------------------------------------------- helper command
cat > "$HOME_DIR/kalshi" <<HELPER
#!/bin/bash
# Kalshi Edge helper (installed by mac/setup.sh). Usage: ~/KalshiEdge/kalshi <command>
set -uo pipefail
H="$HOME_DIR"; L="$LABEL"; D="gui/\$(id -u)"; T="$TS_PORT"
py() { (cd "\$H/app" && KALSHI_EDGE_ENV_FILE="\$H/kalshi-edge.env" "\$H/venv/bin/python" "\$@"); }
case "\${1:-}" in
  status)
    launchctl print "\$D/\$L" 2>/dev/null | grep -E "state =|pid =|last exit code" \
      || echo "Service not loaded. Run: ~/KalshiEdge/kalshi update"
    py -m scripts.trader_cli status ;;
  stop) py -m scripts.trader_cli stop ;;
  paper) py -m scripts.trader_cli paper ;;
  mode) py -m scripts.trader_cli mode ;;
  restart) launchctl kickstart -k "\$D/\$L" && echo "Restarted." ;;
  logs) tail -n 100 -f "\$H/logs/server.log" ;;
  smoke) SCHEDULER_ENABLED=false py -m scripts.smoke_live ;;
  update)
    tmp="\$(mktemp -d)"
    curl -fsSL -o "\$tmp/main.zip" https://github.com/DustinBieber/fantasy-labs/archive/refs/heads/main.zip \
      && unzip -q "\$tmp/main.zip" -d "\$tmp" && bash "\$tmp/fantasy-labs-main/kalshi-edge/mac/setup.sh" \${2+"\${@:2}"}
    rm -rf "\$tmp" ;;
  uninstall)
    launchctl bootout "\$D/\$L" 2>/dev/null; rm -f "\$HOME/Library/LaunchAgents/\$L.plist"
    for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
      command -v "\$c" >/dev/null 2>&1 && { "\$c" serve --https="\$T" off 2>/dev/null; break; }
    done
    echo "Service removed. Your settings and data are still in \$H (delete that folder to erase them)." ;;
  *) echo "Usage: ~/KalshiEdge/kalshi status|stop|paper|restart|logs|smoke|update|uninstall"; exit 1 ;;
esac
HELPER
chmod 700 "$HOME_DIR/kalshi"

# ---------------------------------------------------------------- live data check (first run, or --smoke)
if [ "$RUN_SMOKE" = 1 ]; then
  say "Checking every live data source once (a few minutes: it scans all of Kalshi)"
  (cd "$HOME_DIR/app" && KALSHI_EDGE_ENV_FILE="$ENV_FILE" SCHEDULER_ENABLED=false "$VPY" -m scripts.smoke_live \
     >"$HOME_DIR/logs/smoke.txt" 2>&1) || warn "The check stopped early; see $HOME_DIR/logs/smoke.txt"
  sed -n '/^VERIFY/,/^Data freshness/p' "$HOME_DIR/logs/smoke.txt" | sed '$d'
  echo "Full report: $HOME_DIR/logs/smoke.txt"
fi

# ---------------------------------------------------------------- background service
write_plist() {
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$VPY</string><string>-m</string><string>uvicorn</string><string>app.main:app</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME_DIR/app</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HOME_DIR/logs/server.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/logs/server.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>KALSHI_EDGE_ENV_FILE</key><string>$ENV_FILE</string>
  </dict>
</dict>
</plist>
PLIST
}

restart_service() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  for _ in $(seq 1 30); do
    case "$(http_code)" in 200|401) return 0 ;; esac
    sleep 1
  done
  warn "The server didn't come up. Last log lines:"; tail -n 20 "$HOME_DIR/logs/server.log" || true
  return 1
}

say "Starting the Kalshi Edge background service"
write_plist
if ! restart_service; then
  if [ -d "$HOME_DIR/app.old" ]; then
    warn "Rolling back to the previous version, which was working."
    rm -rf "$HOME_DIR/app.failed" && mv "$HOME_DIR/app" "$HOME_DIR/app.failed" && mv "$HOME_DIR/app.old" "$HOME_DIR/app"
    restart_service && echo "Previous version running again on port $PORT."
  fi
  exit 1
fi
echo "Running on port $PORT."
rm -rf "$HOME_DIR/app.old" "$HOME_DIR/app.failed"

# ---------------------------------------------------------------- AI trader: paper mode
MODE="$("$HOME_DIR/kalshi" mode 2>/dev/null || true)"
if [ "$MODE" = "off" ]; then
  say "AI trader"
  if ask "Turn the AI trader on in PAPER mode now? Real prices, pretend money; starts the 2-week go-live test."; then
    "$HOME_DIR/kalshi" paper
  fi
elif [ "$MODE" = "paper" ] || [ "$MODE" = "live" ]; then
  echo "AI trader is in $(echo "$MODE" | tr '[:lower:]' '[:upper:]') mode (unchanged)."
fi

# ---------------------------------------------------------------- private HTTPS for your devices (Tailscale Serve)
say "Address for your MacBook and phone"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
[ -n "$LAN_IP" ] && echo "At home (same Wi-Fi):  http://$LAN_IP:$PORT"
TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
  if command -v "$c" >/dev/null 2>&1; then TS="$(command -v "$c")"; break; fi
done
if [ -n "$TS" ]; then
  # Serve (tailnet-only) on its own port. Family Hub's public Funnel on 443 is left exactly as it is.
  if "$TS" serve --bg --https="$TS_PORT" "http://127.0.0.1:$PORT" >/dev/null 2>&1; then
    HOST="$("$TS" status --json | "$VPY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    echo "Anywhere (your Tailscale devices only):  https://$HOST:$TS_PORT"
    echo "Install the Tailscale app on your phone and MacBook (same account) to open it."
  else
    warn "Tailscale didn't set up the private address. In the Tailscale admin console, turn on MagicDNS and"
    warn "HTTPS Certificates, then re-run this script."
  fi
else
  echo "Optional: install Tailscale (https://tailscale.com/download/mac) and re-run this script for a private"
  echo "https address that works away from home."
fi

# ---------------------------------------------------------------- stay awake
say "Keeping the Mac awake"
if [ "$(pmset -g | awk '/ sleep /{print $2; exit}')" = "0" ]; then
  echo "System sleep is already off."
elif ask "Turn off system sleep and auto-restart after a power cut? (asks for your Mac password)"; then
  sudo pmset -a sleep 0 disksleep 0 womp 1 autorestart 1
  sudo pmset -a displaysleep 10
fi

cat <<TXT

Done.$([ "$FIRST_RUN" = 1 ] && echo " If macOS asks whether Python may accept incoming network connections, click Allow.")
  • Open the address above and sign in with any username and your dashboard password.
  • Check on it from Terminal:  ~/KalshiEdge/kalshi status      Emergency stop:  ~/KalshiEdge/kalshi stop
  • Update to the latest version:  ~/KalshiEdge/kalshi update   (your data and settings are kept)
  • Settings and keys: $ENV_FILE   (after editing it: ~/KalshiEdge/kalshi restart)
  • Logs: $HOME_DIR/logs/server.log
TXT
