#!/bin/bash
# Family Hub — one-line install and update for an always-on Mac:
#
#   curl -fsSL https://raw.githubusercontent.com/DustinBieber/fantasy-labs/main/family-hub/mac/install.sh | bash
#
# Downloads the latest code from GitHub into ~/fantasy-labs (keeping the previous copy as
# ~/fantasy-labs.previous) and runs mac/setup.sh. The nightly auto-update job runs this same script
# with FAMILY_HUB_UPDATE=1, which skips everything when nothing changed on GitHub.
set -euo pipefail

REPO="${FAMILY_HUB_REPO:-DustinBieber/fantasy-labs}"
BRANCH="${FAMILY_HUB_BRANCH:-main}"
SRC="${FAMILY_HUB_SRC:-$HOME/fantasy-labs}"
DATA_DIR="${FAMILY_HUB_HOME:-$HOME/FamilyHub}"

[ "$(uname)" = "Darwin" ] || { echo "Run this on the Mac that will host Family Hub."; exit 1; }

latest="$(curl -fsSL -H 'Accept: application/vnd.github.sha' "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null || true)"
installed="$(cat "$DATA_DIR/version" 2>/dev/null || true)"
if [ "${FAMILY_HUB_UPDATE:-}" = "1" ] && [ -n "$latest" ] && [ "$latest" = "$installed" ]; then
  echo "$(date '+%F %T') Family Hub is up to date (${latest:0:7})."
  exit 0
fi

echo "==> Downloading Family Hub ${latest:0:7} from github.com/$REPO"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/family-hub.zip" "https://github.com/$REPO/archive/refs/heads/$BRANCH.zip"
unzip -q "$TMP/family-hub.zip" -d "$TMP/src"
NEW="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -f "$NEW/family-hub/mac/setup.sh" ] || { echo "The download doesn't look like Family Hub; nothing changed."; exit 1; }

if [ -d "$SRC" ]; then
  rm -rf "${SRC:?}.previous"
  mv "$SRC" "$SRC.previous"
fi
mv "$NEW" "$SRC"

FAMILY_HUB_VERSION="$latest" exec bash "$SRC/family-hub/mac/setup.sh" "$@"
