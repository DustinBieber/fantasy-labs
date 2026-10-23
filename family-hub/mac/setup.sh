#!/bin/bash
# Family Hub — one-command setup for an always-on Mac. Safe to re-run: it also updates an existing install.
#
#   bash family-hub/mac/setup.sh
#
# What it does:
#   1. Gets Python 3.10+ (uses yours, or installs a private copy with `uv` — no admin rights needed)
#   2. Copies the app to ~/FamilyHub and installs its libraries
#   3. Creates ~/FamilyHub/family-hub.env with a fresh encryption key (first run only)
#   4. Installs a LaunchAgent so the hub starts at login and restarts if it ever crashes
#   5. If Tailscale is installed, publishes the hub at a free https://<mac>.<tailnet>.ts.net address
#   6. Optionally stops the Mac from sleeping
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${FAMILY_HUB_HOME:-$HOME/FamilyHub}"
PORT="${FAMILY_HUB_PORT:-8000}"
LABEL="com.familyhub.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$DATA_DIR/family-hub.env"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"

say()  { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!!  %s\033[0m\n" "$*"; }
ask()  { local a=""; { read -r -p "$1 [y/N] " a </dev/tty; } 2>/dev/null || true; [ "$a" = "y" ] || [ "$a" = "Y" ]; }
set_env() {  # set_env KEY VALUE — replace or append a line in the env file
  local tmp; tmp="$(mktemp)"
  grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
  printf '%s="%s"\n' "$1" "$2" >> "$tmp"
  mv "$tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
}

[ "$(uname)" = "Darwin" ] || { echo "Run this on the Mac that will host Family Hub."; exit 1; }
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/backups"

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

# ---------------------------------------------------------------- app + libraries
say "Installing Family Hub into $DATA_DIR"
# A copy outside Desktop/Documents/Downloads avoids macOS privacy blocks for background processes.
rm -rf "$DATA_DIR/app" && mkdir -p "$DATA_DIR/app"
cp -R "$SRC_DIR/app" "$SRC_DIR/static" "$SRC_DIR/requirements.txt" "$DATA_DIR/app/"
[ -x "$DATA_DIR/venv/bin/python" ] || "$PY" -m venv "$DATA_DIR/venv"
"$DATA_DIR/venv/bin/pip" install -q --upgrade pip
"$DATA_DIR/venv/bin/pip" install -q -r "$DATA_DIR/app/requirements.txt"

# ---------------------------------------------------------------- settings
if [ ! -f "$ENV_FILE" ]; then
  say "Creating $ENV_FILE"
  : > "$ENV_FILE"
  set_env FAMILY_HUB_SECRET_KEY "$("$DATA_DIR/venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  set_env FAMILY_HUB_BASE_URL "http://localhost:$PORT"
  set_env FAMILY_HUB_DATABASE_URL "sqlite:///$DATA_DIR/family_hub.db"
  set_env FAMILY_HUB_KEY_FILE "$DATA_DIR/.family_hub_secret"
  set_env FAMILY_HUB_BACKUP_DIR "$DATA_DIR/backups"
  set_env FAMILY_HUB_FILES_DIR "$DATA_DIR/files"
  set_env GOOGLE_CLIENT_ID ""
  set_env GOOGLE_CLIENT_SECRET ""
  if [ -d "$ICLOUD" ] && ask "Save homework/milestone/health attachments in iCloud Drive (shows up in the Files app on your iPhones)?"; then
    mkdir -p "$ICLOUD/Family Hub"
    set_env FAMILY_HUB_FILES_DIR "$ICLOUD/Family Hub"
    warn "If macOS asks whether python may access iCloud Drive, click Allow."
  fi
  echo "Back up this file somewhere safe — it holds the key that encrypts your connected accounts."
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
    <string>$DATA_DIR/venv/bin/uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
    <string>--env-file</string><string>$ENV_FILE</string>
    <string>--proxy-headers</string>
  </array>
  <key>WorkingDirectory</key><string>$DATA_DIR/app</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DATA_DIR/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DATA_DIR/logs/server.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict>
</plist>
PLIST
}

restart_service() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1 && return 0
    sleep 1
  done
  warn "The server didn't come up. Last log lines:"; tail -n 20 "$DATA_DIR/logs/server.log" || true
  return 1
}

say "Starting the Family Hub background service"
write_plist
restart_service && echo "Running at http://localhost:$PORT"

# ---------------------------------------------------------------- HTTPS for phones (Tailscale Funnel)
say "HTTPS address for phones"
TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
  if command -v "$c" >/dev/null 2>&1; then TS="$(command -v "$c")"; break; fi
done
if [ -n "$TS" ]; then
  echo "Publishing with Tailscale Funnel (follow the link it prints if Funnel isn't enabled yet)…"
  if "$TS" funnel --bg "$PORT"; then
    HOST="$("$TS" status --json | "$DATA_DIR/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    URL="https://$HOST"
    set_env FAMILY_HUB_BASE_URL "$URL"
    restart_service >/dev/null && echo "Family Hub is live at: $URL"
  else
    warn "Tailscale Funnel didn't start. Re-run this script after enabling Funnel."
  fi
else
  cat <<TXT
Phones need an https:// address to install the app, get notifications and share location.
Free option (nothing to install on the phones):
  1. Install Tailscale on this Mac: https://tailscale.com/download/mac  (free Personal plan) and sign in.
  2. Re-run:  bash "$SRC_DIR/mac/setup.sh"
     It turns on Tailscale Funnel and prints your permanent https://<mac>.<tailnet>.ts.net address.
TXT
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

Done. Next steps:
  • Open $(grep '^FAMILY_HUB_BASE_URL=' "$ENV_FILE" | cut -d'"' -f2) and create your family.
  • Make sure this Mac logs in automatically after a restart: System Settings → Users & Groups →
    "Automatically log in as…" (needs FileVault off), or log in once after every restart.
  • The first time the hub sends an iMessage, runs a Shortcut, or scans your Wi-Fi, macOS shows a
    permission prompt on this Mac's screen. Click Allow / OK.
  • Logs: $DATA_DIR/logs/server.log   Backups: $DATA_DIR/backups   Update: re-run this script.
TXT
