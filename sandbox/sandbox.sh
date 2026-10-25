#!/usr/bin/env bash
# Fantasy Labs side-hustle sandbox for the Mac mini.
# Runs the clipper in Docker (default) or natively under launchd. bash 3.2 compatible (macOS default).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SANDBOX_DATA="${SANDBOX_DATA:-$HOME/sandbox-data}"
DATA="$SANDBOX_DATA/clipper"
VENV="$SANDBOX_DATA/venvs/clipper"
LABEL="com.fantasylabs.clipper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
MODE_FILE="$SANDBOX_DATA/.mode"
export SANDBOX_DATA

say()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

mode() { cat "$MODE_FILE" 2>/dev/null || echo docker; }
compose() { (cd "$HERE" && docker compose "$@"); }

need_brew() {
  has brew || die "Homebrew is required: https://brew.sh"
}

need_docker() {
  has docker || die "No docker CLI. Install Docker Desktop (docker.com) or colima, or use: $0 setup native"
  docker info >/dev/null 2>&1 || die "Docker is not running. Open Docker Desktop (or run: colima start --cpu 4 --memory 8)"
}

clipper() {
  if [ "$(mode)" = native ]; then
    CLIPPER_DATA="$DATA" "$VENV/bin/clipper" "$@"
  elif compose ps --status running --services 2>/dev/null | grep -q '^clipper$'; then
    compose exec clipper clipper "$@"
  else
    compose run --rm --no-deps clipper clipper "$@"
  fi
}

install_fonts() {
  if [ "$(uname)" = Darwin ] && has brew && ! ls "$HOME/Library/Fonts" /Library/Fonts 2>/dev/null | grep -qi montserrat; then
    say "Installing the Montserrat caption font"
    brew install --cask font-montserrat || warn "Font install failed; captions will use a fallback font."
  fi
}

keep_awake_tip() {
  cat <<'EOF'

Keep the Mac mini awake for the workday (run once, needs your password):
  sudo pmset -c sleep 0 disksleep 0              # never sleep on power
  sudo pmset repeat wakeorpoweron MTWRF 07:45:00 # wake before work hours, just in case
EOF
}

cmd_setup() {
  local want="${1:-docker}"
  mkdir -p "$DATA"
  echo "$want" > "$MODE_FILE"
  if [ "$want" = native ]; then
    cmd_native_install
  else
    need_docker
    say "Building the clipper image (first build takes a few minutes)"
    compose build
    say "Creating config and secrets in $DATA"
    compose run --rm --no-deps clipper clipper init
    install_fonts
  fi
  cat <<EOF

Next:
  1. Add your keys:   open -e "$DATA/.env"        (ANTHROPIC_API_KEY is optional; blank = free mode)
  2. Review settings: open -e "$DATA/config.yaml"
  3. Start it:        $0 up
  4. Check it:        $0 doctor
EOF
  keep_awake_tip
}

cmd_native_install() {
  [ "$(uname)" = Darwin ] || die "native mode is for macOS; use Docker elsewhere"
  need_brew
  say "Installing ffmpeg and python via Homebrew"
  brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
  has python3.12 || brew install python@3.12
  install_fonts
  say "Creating virtualenv at $VENV"
  mkdir -p "$(dirname "$VENV")" "$DATA/logs"
  [ -x "$VENV/bin/python" ] || python3.12 -m venv "$VENV"
  local extras=whisper
  [ "$(uname -m)" = arm64 ] && extras=mlx      # Apple Silicon: fast local Whisper via MLX
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e "$REPO/clipper[$extras]"
  CLIPPER_DATA="$DATA" "$VENV/bin/clipper" init
  echo native > "$MODE_FILE"
  say "Installing LaunchAgent $LABEL"
  mkdir -p "$(dirname "$PLIST")"
  sed -e "s#__VENV__#$VENV#g" -e "s#__DATA__#$SANDBOX_DATA#g" "$HERE/launchd/$LABEL.plist" > "$PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  say "Running natively. Dashboard: http://localhost:8787"
}

cmd_native_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo docker > "$MODE_FILE"
  say "LaunchAgent removed (your data in $DATA is untouched)"
}

cmd_up() {
  if [ "$(mode)" = native ]; then
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
  else
    need_docker
    compose up -d --build
  fi
  say "Dashboard: http://localhost:8787  (login: see DASHBOARD_PASSWORD in $DATA/.env)"
}

cmd_restart() {
  if [ "$(mode)" = native ]; then
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
  else
    compose restart clipper
  fi
  say "Restarted (config.yaml and .env reloaded)"
}

cmd_down() {
  if [ "$(mode)" = native ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    say "Stopped. '$0 native-install' starts it again."
  else
    compose down
  fi
}

cmd_logs() {
  if [ "$(mode)" = native ]; then
    tail -n 100 -f "$DATA/logs/clipper.log"
  else
    compose logs -f --tail 100
  fi
}

cmd_status() {
  echo "mode: $(mode)   data: $DATA"
  if [ "$(mode)" = native ]; then
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state =|pid =' || echo "not loaded"
  else
    compose ps
  fi
  curl -fsS http://127.0.0.1:8787/healthz && echo || warn "dashboard not responding"
}

cmd_update() {
  say "Pulling the latest code"
  git -C "$REPO" pull --ff-only
  if [ "$(mode)" = native ]; then
    "$VENV/bin/pip" install -q -e "$REPO/clipper[$( [ "$(uname -m)" = arm64 ] && echo mlx || echo whisper )]"
    "$VENV/bin/pip" install -q --upgrade yt-dlp
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
  else
    compose build --pull --no-cache clipper   # also picks up the newest yt-dlp
    compose up -d
  fi
}

usage() {
  cat <<EOF
Fantasy Labs sandbox

  $0 setup [docker|native]   one-time setup (default: docker)
  $0 up | down               start / stop
  $0 restart                 reload after editing config.yaml or .env
  $0 status | logs           health and logs
  $0 doctor                  check tools, keys and config
  $0 update                  pull code, update yt-dlp, restart
  $0 open                    open the dashboard
  $0 cli <args>              any clipper command, e.g. '$0 cli sync' or '$0 cli review'
  $0 native-install | native-uninstall

Data, config and secrets live in $DATA
EOF
}

cmd="${1:-help}"
[ $# -gt 0 ] && shift
case "$cmd" in
  setup) cmd_setup "$@" ;;
  up|start) cmd_up ;;
  down|stop) cmd_down ;;
  restart) cmd_restart ;;
  logs) cmd_logs ;;
  status) cmd_status ;;
  doctor) clipper doctor ;;
  update) cmd_update ;;
  open) open "http://localhost:8787" ;;
  cli) clipper "$@" ;;
  native-install) cmd_native_install ;;
  native-uninstall) cmd_native_uninstall ;;
  *) usage ;;
esac
