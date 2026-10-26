#!/bin/bash
# One-time setup on the Mac mini: choose the dashboard password, install the background service
# (starts at boot, reachable from your MacBook and phone) and the nightly auto-update, then open the
# Setup page.
#   ./run/setup-mac.sh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
command -v python3.13 >/dev/null || command -v python3.12 >/dev/null || command -v python3.11 >/dev/null || {
  echo "Python 3.11+ is missing. Run: brew install python@3.12   (Homebrew: https://brew.sh)"; exit 1; }
[ -f .env ] || cp .env.example .env
if ! grep -Eq '^OPSHUB_PASSWORD=[^[:space:]#]' .env; then
  read -r -s -p "Choose a password for the Ops Hub dashboard (no spaces): " PW; echo
  [ ${#PW} -ge 8 ] || { echo "Use at least 8 characters."; exit 1; }
  case "$PW" in *" "*) echo "No spaces, please."; exit 1;; esac
  /usr/bin/python3 - "$PW" <<'PY'
import pathlib, re, sys
p = pathlib.Path(".env")
s = p.read_text()
line = "OPSHUB_PASSWORD=" + sys.argv[1]
s = re.sub(r"(?m)^OPSHUB_PASSWORD=.*$", lambda m: line, s) if re.search(r"(?m)^OPSHUB_PASSWORD=", s) else s + "\n" + line + "\n"
p.write_text(s)
PY
  echo "Saved. You'll log in as 'ops' with that password."
fi
echo "Installing the background service (macOS asks for your Mac login password)..."
PORT="${OPSHUB_PORT:-8000}"
sudo OPSHUB_HOST=0.0.0.0 OPSHUB_PORT="$PORT" ./run/install-mac-service.sh --boot
sudo OPSHUB_PORT="$PORT" ./run/install-mac-service.sh --auto-update
printf "Starting"
for _ in $(seq 1 90); do curl -sf http://localhost:$PORT/healthz >/dev/null && break; printf "."; sleep 1; done; echo
if curl -sf http://localhost:$PORT/healthz >/dev/null; then
  echo "Ops Hub is up: http://localhost:$PORT here, http://$(scutil --get LocalHostName 2>/dev/null || hostname -s).local:$PORT from the MacBook."
  open "http://localhost:$PORT/#setup"
else
  echo "It didn't start. Last log lines:"; tail -n 30 logs/opshub.log 2>/dev/null || true; exit 1
fi
