#!/bin/bash
# Double-click in Finder to start Ops Hub on this Mac, then open http://localhost:8000
# First run installs everything (needs Python 3.11+: https://www.python.org/downloads/).
# sudo and Finder start with a bare PATH; Homebrew lives here on Apple-silicon and Intel Macs.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.." || exit 1
PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Ops Hub..."
  "$PY" -m venv .venv && .venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -e . || {
    echo "Setup failed. Is Python 3.11 or newer installed?"; read -r; exit 1; }
fi
[ -f .env ] || cp .env.example .env
(sleep 3 && open "http://localhost:8000/#setup") &
echo "Ops Hub is running at http://localhost:8000  (close this window to stop)"
exec .venv/bin/python -m opshub serve --host "${OPSHUB_HOST:-127.0.0.1}" --port "${OPSHUB_PORT:-8000}"
