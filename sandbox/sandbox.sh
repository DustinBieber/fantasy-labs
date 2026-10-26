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
compose() { (cd "$HERE" && docker compose ${1+"$@"}); }

need_brew() {
  has brew || die "Homebrew is required: https://brew.sh"
}

need_docker() {
  has docker || die "No docker CLI. Install Docker Desktop (docker.com) or colima, or use: $0 setup native"
  docker info >/dev/null 2>&1 || die "Docker is not running. Open Docker Desktop (or run: colima start --cpu 4 --memory 8)"
}

# Homebrew's plain ffmpeg (slimmed in 2026) has no libass, so native mode uses ffmpeg-full for burned-in captions.
ffbin() {
  local p
  p="$(brew --prefix ffmpeg-full 2>/dev/null || true)/bin"
  if [ -x "$p/ffmpeg" ]; then echo "$p"; else dirname "$(command -v ffmpeg 2>/dev/null || echo /opt/homebrew/bin/ffmpeg)"; fi
}
ffmpeg_ok() { "$1/ffmpeg" -hide_banner -filters 2>/dev/null | grep ' subtitles ' >/dev/null; }

ensure_ffmpeg() {
  ffmpeg_ok "$(ffbin)" && return 0
  say "Installing ffmpeg-full (Homebrew's ffmpeg with libass, needed for captions)"
  brew install ffmpeg-full || { brew unlink ffmpeg >/dev/null 2>&1 || true; brew install ffmpeg-full; }
  ffmpeg_ok "$(ffbin)" || die "ffmpeg still has no 'subtitles' filter. Try: brew reinstall ffmpeg-full"
}

clipper() {
  if [ "$(mode)" = native ]; then
    PATH="$(ffbin):$VENV/bin:$PATH" CLIPPER_DATA="$DATA" "$VENV/bin/clipper" ${1+"$@"}
  elif compose ps --status running --services 2>/dev/null | grep '^clipper$' >/dev/null; then
    compose exec clipper clipper ${1+"$@"}
  else
    compose run --rm --no-deps clipper clipper ${1+"$@"}
  fi
}

# Local Whisper fallback: MLX on Apple Silicon. CLIPPER_EXTRAS= (empty) skips it for a faster install.
extras_spec() {
  local e
  if [ "$(uname -m)" = arm64 ]; then e="${CLIPPER_EXTRAS-mlx}"; else e="${CLIPPER_EXTRAS-whisper}"; fi
  [ -n "$e" ] && echo "[$e]" || true
}

agent_loaded() { launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; }

install_fonts() {
  if [ "$(uname)" = Darwin ] && has brew && ! ls "$HOME/Library/Fonts" /Library/Fonts 2>/dev/null | grep -i montserrat >/dev/null; then
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
  [ -n "${SANDBOX_BOOTSTRAP:-}" ] && return 0
  local start="$0 up"
  [ "$want" = native ] && start="$0 restart   (it's already running; this loads your edits)"
  cat <<EOF

Next:
  1. Add your keys:   open -e "$DATA/.env"        (ANTHROPIC_API_KEY is optional; blank = free mode)
  2. Review settings: open -e "$DATA/config.yaml"
  3. Start it:        $start
  4. Check it:        $0 doctor
EOF
  keep_awake_tip
}

cmd_native_install() {
  [ "$(uname)" = Darwin ] || die "native mode is for macOS; use Docker elsewhere"
  need_brew
  say "Installing ffmpeg and python via Homebrew"
  ensure_ffmpeg
  has python3.12 || brew install python@3.12
  install_fonts
  say "Creating virtualenv at $VENV"
  mkdir -p "$(dirname "$VENV")" "$DATA/logs"
  [ -x "$VENV/bin/python" ] || python3.12 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e "$REPO/clipper$(extras_spec)"
  CLIPPER_DATA="$DATA" "$VENV/bin/clipper" init
  echo native > "$MODE_FILE"
  say "Installing LaunchAgent $LABEL"
  mkdir -p "$(dirname "$PLIST")"
  sed -e "s#__VENV__#$VENV#g" -e "s#__DATA__#$SANDBOX_DATA#g" -e "s#__FFBIN__#$(ffbin)#g" \
    "$HERE/launchd/$LABEL.plist" > "$PLIST"
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
    if agent_loaded; then
      launchctl kickstart -k "gui/$(id -u)/$LABEL"
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST"
    fi
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
    say "Stopped. '$0 up' starts it again."
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
  local quiet="${1:-}" branch before after stamp ytdlp_due=""
  branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  git -C "$REPO" fetch -q origin "$branch"
  before="$(git -C "$REPO" rev-parse HEAD)"
  after="$(git -C "$REPO" rev-parse "origin/$branch")"
  stamp="$SANDBOX_DATA/.ytdlp-updated"           # YouTube changes often; refresh yt-dlp once a day
  if [ ! -f "$stamp" ] || [ -n "$(find "$stamp" -mtime +0 2>/dev/null)" ]; then ytdlp_due=1; fi
  if [ "$quiet" = "--if-changed" ] && [ "$before" = "$after" ] && [ -z "$ytdlp_due" ]; then
    return 0
  fi
  echo "$(date '+%F %T') update: ${before:0:7} -> ${after:0:7}${ytdlp_due:+ (+ yt-dlp refresh)}"
  git -C "$REPO" merge -q --ff-only "origin/$branch" || die "Local edits block the update. See: git -C $REPO status"
  if [ "$(mode)" = native ]; then
    "$VENV/bin/pip" install -q -e "$REPO/clipper$(extras_spec)"
    if [ -n "$ytdlp_due" ]; then "$VENV/bin/pip" install -q --upgrade "yt-dlp[default]" && touch "$stamp"; fi
    if agent_loaded; then launchctl kickstart -k "gui/$(id -u)/$LABEL"; fi
  else
    if [ -n "$ytdlp_due" ]; then compose build --pull --no-cache clipper && touch "$stamp"; else compose build clipper; fi
    compose up -d
  fi
}

UPDATE_LABEL="com.fantasylabs.sandbox-update"
UPDATE_PLIST="$HOME/Library/LaunchAgents/$UPDATE_LABEL.plist"

cmd_autoupdate() {
  if [ "${1:-on}" = off ]; then
    launchctl bootout "gui/$(id -u)/$UPDATE_LABEL" 2>/dev/null || true
    rm -f "$UPDATE_PLIST"
    say "Auto-update off"
    return 0
  fi
  mkdir -p "$DATA/logs" "$(dirname "$UPDATE_PLIST")"
  cat > "$UPDATE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$UPDATE_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$HERE/sandbox.sh</string><string>update</string><string>--if-changed</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>SANDBOX_DATA</key><string>$SANDBOX_DATA</string>
  </dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>$DATA/logs/update.log</string>
  <key>StandardErrorPath</key><string>$DATA/logs/update.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$UPDATE_LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$UPDATE_PLIST"
  say "Auto-update on: checks GitHub hourly and restarts only when something changed (log: $DATA/logs/update.log)"
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
  $0 autoupdate [on|off]      hourly self-update from GitHub (on by default after bootstrap)
  $0 run-now                 start a clipping pass now, even outside work hours
  $0 open                    open the dashboard
  $0 cli <args>              any clipper command, e.g. '$0 cli sync' or '$0 cli review'
  $0 native-install | native-uninstall

Data, config and secrets live in $DATA
EOF
}

cmd="${1:-help}"
[ $# -gt 0 ] && shift
case "$cmd" in
  setup) cmd_setup ${1+"$@"} ;;
  up|start) cmd_up ;;
  down|stop) cmd_down ;;
  restart) cmd_restart ;;
  logs) cmd_logs ;;
  status) cmd_status ;;
  doctor) clipper doctor ;;
  update) cmd_update ${1+"$@"} ;;
  autoupdate) cmd_autoupdate ${1+"$@"} ;;
  run-now) clipper run-now ;;
  open) open "http://localhost:8787" ;;
  cli) clipper ${1+"$@"} ;;
  native-install) cmd_native_install ;;
  native-uninstall) cmd_native_uninstall ;;
  *) usage ;;
esac
