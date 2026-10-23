#!/usr/bin/env bash
# Kalshi Edge: one command to set up and run.
#   ./start.sh          install (first run only) and start the dashboard
#   ./start.sh test     run the test suite
#   ./start.sh smoke    pull live public data once and print sample output
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (add ODDS_API_KEY there if you have one)."
fi

if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv -q .venv --python 3.12
  else
    PY=""
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        PY="$c"; break
      fi
    done
    if [ -z "$PY" ]; then
      echo "Python 3.10 or newer is required. On a Mac: brew install python@3.12   (then run ./start.sh again)"
      exit 1
    fi
    "$PY" -m venv .venv
  fi
fi

REQ_STAMP=.venv/.requirements.installed
if [ ! -f "$REQ_STAMP" ] || [ requirements.txt -nt "$REQ_STAMP" ]; then
  echo "Installing Python packages (first run takes about a minute)..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install -q --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -r requirements.txt
  fi
  touch "$REQ_STAMP"
fi

case "${1:-run}" in
  test)
    exec .venv/bin/python -m pytest -q
    ;;
  smoke)
    exec .venv/bin/python -m scripts.smoke_live "${@:2}"
    ;;
  run)
    PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || true)
    HOST=$(grep -E '^APP_HOST=' .env | cut -d= -f2 | tr -d '[:space:]' || true)
    PORT=${PORT:-8000}
    HOST=${HOST:-0.0.0.0}
    IP=$( (ipconfig getifaddr en0 || ipconfig getifaddr en1 || hostname -I | awk '{print $1}') 2>/dev/null || true)
    echo ""
    echo "  Kalshi Edge is starting."
    echo "  On this computer:          http://localhost:${PORT}"
    [ -n "$IP" ] && echo "  On your phone / MacBook:   http://${IP}:${PORT}   (same Wi-Fi)"
    echo "  Stop with Ctrl+C."
    echo ""
    exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
    ;;
  *)
    echo "Usage: ./start.sh [run|test|smoke]"; exit 1
    ;;
esac
