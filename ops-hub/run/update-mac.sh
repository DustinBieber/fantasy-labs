#!/bin/bash
# Pull the latest Ops Hub from GitHub (branch main), install any new dependencies, restart the service.
# Rolls back to the previous version if the new one doesn't come up healthy.
#   ./run/update-mac.sh           update now (asks for your password to restart a --boot service)
# Runs by itself every night once the auto-update job is installed (install-mac-service.sh --auto-update).
set -euo pipefail
# sudo, Finder and launchd start with a bare PATH; Homebrew lives here on Apple-silicon and Intel Macs.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")/.."
BRANCH="${OPSHUB_BRANCH:-main}"
PORT="${OPSHUB_PORT:-8000}"
OWNER=$(stat -f %Su . 2>/dev/null || stat -c %U .)
# The nightly job runs as root; git and pip must run as the checkout's owner (their GitHub login, their files).
as_owner() { if [ "$(id -u)" = 0 ] && [ "$OWNER" != root ]; then sudo -H -u "$OWNER" "$@"; else "$@"; fi; }
echo "$(date '+%Y-%m-%d %H:%M:%S') checking for updates on $BRANCH"

as_owner git fetch --quiet origin "$BRANCH"
PREV=$(git rev-parse HEAD)
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  as_owner git checkout --quiet -B "$BRANCH" --track "origin/$BRANCH"  # e.g. a clone of an older work branch
elif [ "$PREV" = "$(git rev-parse "origin/$BRANCH")" ]; then
  echo "Already up to date ($(git log -1 --format='%h %s'))."; exit 0
else
  as_owner git merge --quiet --ff-only "origin/$BRANCH"
fi
echo "Updating $(git rev-parse --short "$PREV") -> $(git log -1 --format='%h %s')"

restart() {
  if [ -f /Library/LaunchDaemons/com.opshub.server.plist ]; then
    if [ "$(id -u)" = 0 ]; then launchctl kickstart -k system/com.opshub.server
    else sudo launchctl kickstart -k system/com.opshub.server; fi
  elif [ -f "/Users/$OWNER/Library/LaunchAgents/com.opshub.server.plist" ]; then
    launchctl kickstart -k "gui/$(id -u "$OWNER")/com.opshub.server"
  else
    echo "No background service installed; restart Ops Hub yourself."; return 1
  fi
}
healthy() {
  for _ in $(seq 1 90); do curl -sf http://localhost:$PORT/healthz >/dev/null && return 0; sleep 1; done
  return 1
}

as_owner .venv/bin/pip install --quiet -e .
restart || exit 0
sleep 3  # let the old process exit before asking the new one
if healthy; then
  echo "Updated and restarted. Check http://localhost:$PORT"
else
  echo "The new version didn't start; going back to $(git rev-parse --short "$PREV"). Last log lines:"
  tail -n 30 logs/opshub.log 2>/dev/null || true
  as_owner git reset --quiet --keep "$PREV"
  as_owner .venv/bin/pip install --quiet -e .
  restart && healthy && echo "Back on the previous version." || echo "Still not starting; check logs/opshub.log."
  exit 1
fi
