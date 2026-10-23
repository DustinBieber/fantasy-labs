#!/bin/bash
# Stops and removes the Family Hub background service. Your data in ~/FamilyHub is kept.
set -euo pipefail
LABEL="com.familyhub.server"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
  if command -v "$c" >/dev/null 2>&1; then "$c" funnel reset 2>/dev/null || true; break; fi
done
echo "Family Hub service removed. Data is still in ${FAMILY_HUB_HOME:-$HOME/FamilyHub} (delete it yourself if you want)."
