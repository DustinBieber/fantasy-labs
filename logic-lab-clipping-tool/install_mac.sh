#!/bin/bash
# Logic Lab Clipping Tool: one-time setup on a Mac (Apple Silicon or Intel). About 10 minutes.
# Run it from Terminal:  bash "<this folder>/install_mac.sh"
# Safe to run again; it updates yt-dlp (do that if YouTube downloads start failing).
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME/.logiclab-clipping-tool"   # Python packages live here, outside Google Drive
VENV="$HOME_DIR/venv"

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This installer is for macOS."
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)" && break; done
fi
command -v brew >/dev/null 2>&1 || die "Homebrew is missing. Install it from https://brew.sh, then run this again."
BREW="$(brew --prefix)"

say "1/5 ffmpeg-full (Homebrew's plain ffmpeg has no libass, which burned-in captions need)"
FFBIN="$BREW/opt/ffmpeg-full/bin"
ffmpeg_ok() { "$FFBIN/ffmpeg" -hide_banner -filters 2>/dev/null | grep -q ' subtitles '; }
ffmpeg_ok || brew install ffmpeg-full
ffmpeg_ok || die "ffmpeg-full has no 'subtitles' filter. Try: brew reinstall ffmpeg-full"

say "2/5 Python 3.12 and Deno (Deno lets yt-dlp read YouTube)"
PY=""
for c in python3.12 python3.13; do
  if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then brew install python@3.12; PY="$BREW/bin/python3.12"; fi
command -v deno >/dev/null 2>&1 || brew install deno

say "3/5 Speech engine and downloader (sherpa-onnx, yt-dlp)"
mkdir -p "$HOME_DIR/bin"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "sherpa-onnx==2.0.1" numpy "yt-dlp[default]"

say "4/5 The llclip command"
cat > "$HOME_DIR/bin/llclip" <<EOF
#!/bin/bash
export LLCLIP_FFMPEG="$FFBIN/ffmpeg" LLCLIP_FFPROBE="$FFBIN/ffprobe"
exec "$VENV/bin/python" "$TOOL_DIR/clip.py" "\$@"
EOF
chmod +x "$HOME_DIR/bin/llclip"
if ! grep -qF '.logiclab-clipping-tool/bin' "$HOME/.zshrc" 2>/dev/null; then
  printf '\nexport PATH="$HOME/.logiclab-clipping-tool/bin:$PATH"  # Logic Lab Clipping Tool\n' >> "$HOME/.zshrc"
  echo "Added one line to ~/.zshrc so Terminal knows the llclip command."
fi
export PATH="$HOME_DIR/bin:$PATH"

say "5/5 Speech models (about 480 MB, one time) and a checkup"
llclip setup

cat <<'EOF'

Done. Open a NEW Terminal window, then try:
  llclip transcribe "https://www.youtube.com/watch?v=VIDEO_ID"
  llclip transcribe ~/Downloads/episode.mp4 --name "Footballers Dynasty"
EOF
