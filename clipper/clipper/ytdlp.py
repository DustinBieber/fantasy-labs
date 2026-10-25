"""Thin wrapper around the yt-dlp library: channel tabs, search, captions, downloads."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yt_dlp
from yt_dlp.utils import DownloadError

from .util import log

# YouTube search filter "Duration: over 20 minutes".
LONG_VIDEO_FILTER = "EgIYAg%3D%3D"


class NoShortsTab(Exception):
    pass


def _opts(**extra: Any) -> dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 2,
        "sleep_interval_requests": 0.5,
        "logger": _QuietLogger(),
    }
    opts.update(extra)
    return opts


class _QuietLogger:
    def debug(self, msg):  # yt-dlp routes info through debug
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log.debug("yt-dlp: %s", msg)

    def error(self, msg):
        log.debug("yt-dlp error: %s", msg)


def channel_url(channel: str) -> str:
    """Accept a channel id, @handle, or URL; return the channel's base URL."""
    channel = channel.strip().rstrip("/")
    if channel.startswith("http"):
        return re.sub(r"/(videos|shorts|streams|featured|about)$", "", channel)
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}"
    if channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel}"
    return f"https://www.youtube.com/@{channel}"


def flat_tab(url: str, limit: int, approximate_date: bool = False) -> dict[str, Any]:
    """List a channel tab (videos/shorts/streams) without visiting each video."""
    extra: dict[str, Any] = {"extract_flat": "in_playlist", "playlistend": limit}
    if approximate_date:
        extra["extractor_args"] = {"youtubetab": {"approximate_date": [""]}}
    try:
        with yt_dlp.YoutubeDL(_opts(**extra)) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except DownloadError as e:
        if "does not have a" in str(e) and "tab" in str(e):
            raise NoShortsTab(str(e)) from e
        raise
    info["entries"] = [e for e in (info.get("entries") or []) if e]
    return info


def search(query: str, limit: int, long_only: bool = True) -> list[dict[str, Any]]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    if long_only:
        url += f"&sp={LONG_VIDEO_FILTER}"
    with yt_dlp.YoutubeDL(_opts(extract_flat="in_playlist", playlistend=limit)) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    return [e for e in (info.get("entries") or []) if e][:limit]


def video_info(url: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_opts()) as ydl:
        return ydl.extract_info(url, download=False) or {}


def fetch_captions(video_id: str, workdir: Path, language: str = "en") -> tuple[Path, str] | None:
    """Download YouTube's own captions as json3. Returns (path, kind) or None.

    Auto captions carry per-word timing, which the karaoke subtitles need, so they win
    over uploaded captions when both exist.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    langs = [f"{language}-orig", language, f"{language}-US", f"{language}-GB"]
    opts = _opts(
        skip_download=True,
        writesubtitles=True,
        writeautomaticsub=True,
        subtitleslangs=langs,
        subtitlesformat="json3",
        outtmpl={"default": str(workdir / "%(id)s.%(ext)s")},
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True) or {}
    auto = set((info.get("automatic_captions") or {}).keys())
    for lang in langs:
        path = workdir / f"{video_id}.{lang}.json3"
        if path.exists():
            return path, "youtube-auto" if lang in auto else "youtube-manual"
    return None


def download_video(video_id: str, out_dir: Path, max_height: int = 1080) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = (
        f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    )
    opts = _opts(
        format=fmt,
        merge_output_format="mp4",
        outtmpl={"default": str(out_dir / "%(id)s.%(ext)s")},
        concurrent_fragment_downloads=4,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    for ext in ("mp4", "mkv", "webm"):
        path = out_dir / f"{video_id}.{ext}"
        if path.exists():
            return path
    raise FileNotFoundError(f"yt-dlp finished but no file for {video_id} in {out_dir}")


def download_audio(video_id: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = _opts(format="ba[ext=m4a]/ba", outtmpl={"default": str(out_dir / "%(id)s.audio.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    matches = sorted(out_dir.glob(f"{video_id}.audio.*"))
    if not matches:
        raise FileNotFoundError(f"no audio downloaded for {video_id}")
    return matches[0]
