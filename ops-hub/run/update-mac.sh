#!/bin/bash
# Pull the latest Ops Hub, install any new dependencies, restart the background service.
set -euo pipefail
# sudo and Finder start with a bare PATH; Homebrew lives here on Apple-silicon and Intel Macs.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install --quiet -e .
if [ -f /Library/LaunchDaemons/com.opshub.server.plist ]; then
  sudo launchctl kickstart -k system/com.opshub.server
elif [ -f "$HOME/Library/LaunchAgents/com.opshub.server.plist" ]; then
  launchctl kickstart -k "gui/$(id -u)/com.opshub.server"
else
  echo "No background service installed; restart Ops Hub yourself."; exit 0
fi
echo "Updated and restarted. Check http://localhost:8000"
