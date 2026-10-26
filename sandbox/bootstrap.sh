#!/bin/bash
# One-command setup for the Fantasy Labs sandbox on a Mac mini. Paste this into Terminal:
#
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/DustinBieber/fantasy-labs/main/sandbox/bootstrap.sh)"
#
# Safe to re-run: it updates what's there instead of starting over. bash 3.2 compatible.
set -euo pipefail

REPO_URL="https://github.com/DustinBieber/fantasy-labs.git"
DIR="${FANTASY_LABS_DIR:-$HOME/fantasy-labs}"
BRANCH="${FANTASY_LABS_BRANCH:-main}"
DATA="${SANDBOX_DATA:-$HOME/sandbox-data}/clipper"
TS_APP="/Applications/Tailscale.app/Contents/MacOS/Tailscale"

say()  { printf '\n\033[1;33m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
# Prompts read from the terminal even under curl; with no terminal they answer "" (the default).
ask() {
  local reply=""
  if { exec 3<>/dev/tty; } 2>/dev/null; then
    printf '%s ' "$1" >&3
    read -r reply <&3 || reply=""
    exec 3>&-
  fi
  echo "$reply"
}

[ "$(uname)" = Darwin ] || die "This installer is for macOS (the Mac mini)."
[ "$(uname -m)" = arm64 ] || warn "Intel Mac: everything works, renders are just slower."

load_brew() {
  local b
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}

# 1. Homebrew (also brings Apple's command line tools, including git)
if ! command -v brew >/dev/null 2>&1 && ! load_brew; then
  say "1/6 Installing Homebrew (asks for your Mac password, takes a few minutes)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew || die "Homebrew didn't finish installing. Run this script again."
  grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null || echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"
else
  say "1/6 Homebrew is installed"
fi

# 2. Code
if [ -d "$DIR/.git" ]; then
  say "2/6 Updating the code in $DIR"
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" merge -q --ff-only "origin/$BRANCH"
else
  say "2/6 Downloading the code to $DIR"
  git clone -q -b "$BRANCH" "$REPO_URL" "$DIR"
fi

# 3. Two quick questions up front, so the install can run unattended
say "3/6 Two quick questions"
key=""
if ! grep -q '^ANTHROPIC_API_KEY=..' "$DATA/.env" 2>/dev/null; then
  echo "Claude API key (optional): smarter clip picks for about half a cent per episode. Press Return to run 100% free."
  key="$(ask 'Paste your Claude API key:')"
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
fi
awake=""
if [ "$(pmset -g custom 2>/dev/null | awk '/^AC Power/{f=1} f && $1=="sleep"{print $2; exit}')" != "0" ]; then
  awake="$(ask 'Keep the Mac mini from sleeping so it can work all day? (needs your password) [Y/n]')"
  case "$awake" in
    n|N|no|No) echo "Skipped. Turn it on later: System Settings > Energy > Prevent automatic sleeping." ;;
    *) sudo pmset -c sleep 0 disksleep 0 && sudo pmset repeat wakeorpoweron MTWRF 07:45:00 && echo "Done." ;;
  esac
fi

# 4. Install and start the service (native: launchd, hardware encoder, MLX Whisper)
say "4/6 Installing the clipper (ffmpeg, Python, caption font). About 10 minutes the first time"
SANDBOX_BOOTSTRAP=1 "$DIR/sandbox/sandbox.sh" setup native
ENV_FILE="$DATA/.env"
if [ -n "$key" ]; then
  sed -i '' "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$key|" "$ENV_FILE"
  "$DIR/sandbox/sandbox.sh" restart >/dev/null
  echo "Claude key saved."
fi

# 5. Keep itself up to date from GitHub
say "5/6 Automation"
"$DIR/sandbox/sandbox.sh" autoupdate on

# 6. Phone access through Tailscale, then kick off the first clipping pass
say "6/6 Phone access"
DASH="http://localhost:8787"
if [ -x "$TS_APP" ] && "$TS_APP" status >/dev/null 2>&1; then
  if "$TS_APP" serve --bg 8787; then
    host="$("$TS_APP" status --json 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
    if [ -n "$host" ]; then DASH="https://$host"; fi
  else
    warn "tailscale serve failed. If it printed a link to enable HTTPS, open it, then run: $TS_APP serve --bg 8787"
  fi
else
  echo "Tailscale isn't set up on this Mac yet. Install it (opening the download page), sign in, then run:"
  echo "  $TS_APP serve --bg 8787"
  open "https://tailscale.com/download/mac" || true
fi

sleep 3
"$DIR/sandbox/sandbox.sh" run-now >/dev/null || true
"$DIR/sandbox/sandbox.sh" doctor || true

password="$(sed -n 's/^DASHBOARD_PASSWORD=//p' "$ENV_FILE")"
cat <<EOF

$(printf '\033[1;32m')All set. The clipper is running and its first pass has started.$(printf '\033[0m')

  Dashboard:  $DASH
  Login:      admin / $password

  - Sample clips from The Fantasy Footballers' 2 latest episodes show up under Review in about 10-30 minutes.
  - On your S24: install Tailscale from the Play Store (same account), open the dashboard address in Chrome,
    then use the menu and tap "Add to Home screen".
  - It runs 8am-5pm HST on weekdays, updates itself from GitHub hourly, and restarts after reboots.
  - Logs: $DIR/sandbox/sandbox.sh logs
EOF
open "http://localhost:8787" >/dev/null 2>&1 || true
