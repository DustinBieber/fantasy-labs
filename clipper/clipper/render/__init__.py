"""Render vertical clips with ffmpeg: cut segments, reframe to 9:16, burn in subtitles."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from ..config import Render
from ..transcripts import Word
from .subtitles import build_ass, timeline_words


class RenderError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _encoders() -> str:
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def encoder_args(style: Render) -> list[str]:
    enc = style.encoder
    if enc == "auto":
        enc = "h264_videotoolbox" if sys.platform == "darwin" and "h264_videotoolbox" in _encoders() else "libx264"
    if enc == "h264_videotoolbox":
        # Apple's hardware encoder: far faster on the Mac mini, slightly bigger files.
        return ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", style.preset, "-crf", str(style.crf), "-profile:v", "high"]


def filter_graph(n_segments: int, style: Render, ass_name: str, fonts_rel: str | None) -> str:
    W, H, fps = style.width, style.height, style.fps
    parts = []
    for i in range(n_segments):
        parts.append(f"[{i}:v]fps={fps},setpts=PTS-STARTPTS,setsar=1[v{i}]")
        parts.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    if n_segments > 1:
        pairs = "".join(f"[v{i}][a{i}]" for i in range(n_segments))
        parts.append(f"{pairs}concat=n={n_segments}:v=1:a=1[vc][ac]")
    else:
        parts.append("[v0]null[vc]")
        parts.append("[a0]anull[ac]")

    if style.layout == "crop":
        parts.append(
            f"[vc]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:(iw-ow)*{style.crop_x:.3f}:(ih-oh)/2[base]"
        )
    else:
        bw, bh = W // 4, H // 4
        parts.append("[vc]split=2[bgs][fgs]")
        parts.append(
            f"[bgs]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=8:2,scale={W}:{H},eq=brightness=-0.12[bg]"
        )
        parts.append(f"[fgs]scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg]")
        parts.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")

    subs = f"subtitles=filename={ass_name}"
    if fonts_rel:
        subs += f":fontsdir={fonts_rel}"
    parts.append(f"[base]{subs},format=yuv420p[vout]")
    parts.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]" if style.loudnorm else "[ac]anull[aout]")
    return ";".join(parts)


def render_clip(
    source: Path,
    segments: list[tuple[float, float]],
    words: list[Word],
    out_path: Path,
    style: Render,
    hook: str = "",
    cta: str = "",
    fonts_dir: Path | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = out_path.with_suffix(".work")
    work.mkdir(exist_ok=True)
    duration = sum(e - s for s, e in segments)
    ass = build_ass(timeline_words(words, segments), duration, style, hook=hook, cta=cta)
    (work / "clip.ass").write_text(ass, encoding="utf-8")

    fonts_rel = None
    if fonts_dir and fonts_dir.is_dir() and any(fonts_dir.iterdir()):
        fonts_rel = os.path.relpath(fonts_dir.resolve(), work.resolve())

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    for s, e in segments:
        cmd += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(source.resolve())]
    cmd += ["-filter_complex", filter_graph(len(segments), style, "clip.ass", fonts_rel)]
    cmd += ["-map", "[vout]", "-map", "[aout]"]
    cmd += encoder_args(style)
    cmd += ["-pix_fmt", "yuv420p", "-r", str(style.fps), "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out_path.resolve())]

    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-1500:]}")

    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", "1", "-i", str(out_path),
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", str(out_path.with_suffix(".jpg"))],
        capture_output=True,
    )
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    return out_path


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RenderError(proc.stderr.strip())
    return json.loads(proc.stdout)
