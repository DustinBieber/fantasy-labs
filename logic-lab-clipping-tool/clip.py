#!/usr/bin/env python3
"""Logic Lab Clipping Tool: a long video in, captioned vertical clips out.

    llclip transcribe <video file or link>   Stage 1: get the video, write the transcript
    llclip pick <job>                        Stage 2: not built yet
    llclip render <job>                      Stage 3: not built yet

    llclip setup     download the speech models (about 480 MB, one time) and self-check
    llclip doctor    check the install and say what to fix
    llclip list      show jobs

Where files go:
  Jobs/<date> <name>/   next to this script (inside Google Drive): transcript, job.json,
                        later the moment list and finished clips. Small files only.
  Work folder           ~/Documents/Clipping Service Footage/Clipping Tool Work/
                        downloaded videos and audio. Stays on this Mac, no backup.

Speech engine: sherpa-onnx running NVIDIA Parakeet TDT 0.6B v2 (English) on the CPU.
Everything runs on this computer. No API key, nothing uploaded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"
TOOL_NAME = "Logic Lab Clipping Tool"
TOOL_DIR = Path(__file__).resolve().parent
FOOTAGE = Path.home() / "Documents" / "Clipping Service Footage"

SAMPLE_RATE = 16000
MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
VAD_FILE = "silero_vad.onnx"
ASR_DIR = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
ASR_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
ASR_LABEL = "NVIDIA Parakeet TDT 0.6B v2 (int8) on sherpa-onnx"

# The model card says: split long audio at silences into 15-20 s pieces, never fixed windows.
CHUNK_MAX_S = 15.0      # longest piece sent to the model
JOIN_GAP_S = 0.6        # speech pieces closer than this are decoded together
PAD_S = 0.2             # extra audio kept on each side of a piece so word edges aren't clipped
DECODE_BATCH = 4        # pieces decoded at once; higher is a little faster but uses more memory
LOW_CONFIDENCE = 0.6    # words below this are underlined in transcript.html as "check this"

ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "jr.", "sr.", "etc.", "e.g.", "i.e.",
                 "u.s.", "no.", "inc.", "co.", "corp.", "ltd.", "approx.", "a.m.", "p.m."}
YOUTUBE_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


class Fail(Exception):
    """A problem the user can fix. The message is shown as-is."""


def say(msg: str = "") -> None:
    print(msg, flush=True)


def hms(sec: float) -> str:
    sec = int(max(0.0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def mmss(sec: float) -> str:
    sec = int(round(max(0.0, sec)))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def srt_time(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def now_local() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------------
# Paths and outside tools


@dataclass
class Paths:
    jobs: Path
    work: Path
    models: Path


def resolve_paths(args) -> Paths:
    def pick(flag, envs, default):
        if flag:
            return Path(flag).expanduser().resolve()
        for name in envs:
            if os.environ.get(name):
                return Path(os.environ[name]).expanduser().resolve()
        return default
    return Paths(
        jobs=pick(args.jobs, ["LLCLIP_JOBS"], TOOL_DIR / "Jobs"),
        work=pick(args.work, ["LLCLIP_WORK"], FOOTAGE / "Clipping Tool Work"),
        models=pick(args.models, ["LLCLIP_MODELS", "MODELS"], FOOTAGE / "_engine models"),
    )


def find_tool(name: str) -> str | None:
    """ffmpeg/ffprobe: prefer Homebrew's ffmpeg-full (it has libass, which Stage 3 captions need)."""
    env = os.environ.get(f"LLCLIP_{name.upper()}")
    if env and Path(env).exists():
        return env
    for prefix in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"):
        if (Path(prefix) / name).exists():
            return str(Path(prefix) / name)
    return shutil.which(name)


def need_tool(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise Fail(f"{name} is not installed. On a Mac run the installer (install_mac.sh) "
                   f"or: brew install ffmpeg-full")
    return path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip()[-1500:]
        raise Fail(f"{Path(cmd[0]).name} failed:\n{tail}")
    return r


def js_runtime() -> dict | None:
    """yt-dlp needs a JavaScript runtime (Deno by default) to read YouTube pages."""
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if not path:
            for d in ("/opt/homebrew/bin", "/usr/local/bin"):
                if (Path(d) / name).exists():
                    path = str(Path(d) / name)
                    break
        if path:
            return {name: {"path": path}}
    return None


# --------------------------------------------------------------------------------------------
# Models


def models_ready(models: Path) -> bool:
    vad = models / VAD_FILE
    return vad.exists() and vad.stat().st_size > 500_000 and all(
        (models / ASR_DIR / f).exists() for f in ASR_FILES)


def curl_download(url: str, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".part")
    r = subprocess.run(["curl", "-L", "--fail", "--retry", "3", "--progress-bar", "-o", str(tmp), url])
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise Fail(f"Download failed ({url}). Check the internet connection and run: llclip setup")
    tmp.replace(dest)


def safe_extract(archive: Path, dest: Path) -> None:
    root = dest.resolve()
    with tarfile.open(archive, "r:bz2") as tar:
        keep = []
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if target != root and root not in target.parents:
                raise Fail(f"The model archive has an unsafe path ({m.name}); not extracting it.")
            if m.isfile() or m.isdir():
                keep.append(m)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=keep, filter="data")
        else:
            tar.extractall(dest, members=keep)


def ensure_models(models: Path) -> None:
    if models_ready(models):
        return
    models.mkdir(parents=True, exist_ok=True)
    vad = models / VAD_FILE
    if not (vad.exists() and vad.stat().st_size > 500_000):
        say("Downloading the speech finder (silero VAD, 1 MB)...")
        curl_download(f"{MODEL_URL}/{VAD_FILE}", vad)
    if not all((models / ASR_DIR / f).exists() for f in ASR_FILES):
        say("Downloading the speech model (Parakeet TDT 0.6B v2, about 480 MB, one time)...")
        archive = models / f"{ASR_DIR}.tar.bz2"
        curl_download(f"{MODEL_URL}/{ASR_DIR}.tar.bz2", archive)
        say("Unpacking...")
        safe_extract(archive, models)
        archive.unlink()
    if not models_ready(models):
        raise Fail(f"The models are still incomplete in {models}. Delete that folder's "
                   f"{ASR_DIR} subfolder and run: llclip setup")
    say(f"Models ready in {models}")


def make_recognizer(models: Path, threads: int):
    import sherpa_onnx
    d = models / ASR_DIR
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
        joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"),
        num_threads=threads, model_type="nemo_transducer", decoding_method="greedy_search")


def default_threads() -> int:
    return max(1, min(8, os.cpu_count() or 4))


# --------------------------------------------------------------------------------------------
# Jobs


def slugify(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\\/:*?\"<>|#%{}$`@+=\[\]~^]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip(" .-_")
    return text or "Untitled"


def source_key(src: str) -> tuple[str, str]:
    """(kind, key). kind is 'youtube', 'link' or 'file'. key identifies the source across runs."""
    if re.match(r"^https?://", src, re.I):
        m = YOUTUBE_ID.search(src)
        if m:
            return "youtube", f"youtube:{m.group(1)}"
        return "link", src.strip()
    path = Path(src).expanduser().resolve()
    return "file", f"file:{path}"


def find_job(jobs: Path, key: str) -> Path | None:
    if not jobs.exists():
        return None
    for jf in sorted(jobs.glob("*/job.json")):
        try:
            if json.loads(jf.read_text()).get("source", {}).get("key") == key:
                return jf.parent
        except (OSError, ValueError):
            continue
    return None


def new_job_dir(jobs: Path, name: str) -> Path:
    base = f"{dt.date.today():%Y-%m-%d} {slugify(name)}"
    d, n = jobs / base, 2
    while d.exists():
        d, n = jobs / f"{base} ({n})", n + 1
    d.mkdir(parents=True)
    return d


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------------------------
# Getting the video


def download_link(url: str, paths: Paths, ffmpeg: str) -> tuple[Path, dict]:
    try:
        import yt_dlp
    except ImportError:
        raise Fail("yt-dlp is not installed. Run the installer again (install_mac.sh).")
    dl_dir = paths.work / "Downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    shown: dict[str, int] = {}  # file name -> last percent printed (video and audio come separately)

    def hook(d):
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if not total:
            return
        name = Path(d.get("filename") or "file").name
        pct = int(d.get("downloaded_bytes", 0) * 100 / total) // 20 * 20
        if pct > shown.get(name, -1):
            shown[name] = pct
            say(f"  downloading {pct}%  ({total / 1e6:.0f} MB)")

    opts = {
        "outtmpl": str(dl_dir / "%(title).70B [%(id)s].%(ext)s"),
        # H.264 up to 1080p first: fastest to decode and re-encode on a Mac.
        "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "ffmpeg_location": str(Path(ffmpeg).parent),
        "retries": 5,
        "fragment_retries": 5,
    }
    runtime = js_runtime()
    if runtime:
        opts["js_runtimes"] = runtime
    say(f"Getting the video: {url}")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            files = [d.get("filepath") for d in info.get("requested_downloads") or [] if d.get("filepath")]
            media = Path(files[0]) if files else Path(ydl.prepare_filename(info))
    except Exception as e:  # yt-dlp raises many error types; the message is what matters
        msg = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        hint = ("Download it yourself (browser or your usual downloader), then run:\n"
                "  llclip transcribe \"/path/to/the/video.mp4\"")
        if not runtime and YOUTUBE_ID.search(url):
            hint = "Deno is missing (yt-dlp needs it for YouTube): brew install deno\nOr: " + hint
        raise Fail(f"Could not download that link.\n  {msg}\n{hint}")
    if not media.exists():
        raise Fail(f"yt-dlp finished but the file is missing: {media}")
    meta = {
        "url": info.get("webpage_url") or url,
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "extractor": info.get("extractor_key"),
    }
    return media, {k: v for k, v in meta.items() if v is not None}


def probe(ffprobe: str, media: Path) -> dict:
    r = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(media)])
    j = json.loads(r.stdout or "{}")
    streams = j.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio:
        raise Fail(f"{media.name} has no audio track, so there is nothing to transcribe.")
    out = {"duration": round(float(j.get("format", {}).get("duration") or 0), 3), "has_video": bool(video)}
    if video:
        num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        out.update(width=video.get("width"), height=video.get("height"), fps=round(fps, 3),
                   video_codec=video.get("codec_name"))
    out["audio_codec"] = audio.get("codec_name")
    return out


def extract_audio(ffmpeg: str, media: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("audio16k.part.wav")
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(out)


# --------------------------------------------------------------------------------------------
# Speech to text


def load_wav(path: Path):
    import numpy as np
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise Fail(f"{path.name} is not 16 kHz mono 16-bit. Delete it and run again.")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


VAD_WINDOW = 512  # Silero must be fed exactly this many samples per call; larger feeds mis-segment


def find_speech(samples, vad_path: Path) -> list[tuple[int, int]]:
    """Silero VAD: (start, end) sample ranges that contain speech, each at most CHUNK_MAX_S long."""
    import numpy as np
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(vad_path)
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = CHUNK_MAX_S
    cfg.silero_vad.window_size = VAD_WINDOW
    cfg.sample_rate = SAMPLE_RATE
    cfg.num_threads = 1
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    audio = samples.astype(np.float32) / 32768.0  # convert the whole array once
    found: list[tuple[int, int]] = []

    def drain():
        while not vad.empty():
            seg = vad.front
            found.append((seg.start, seg.start + len(seg.samples)))
            vad.pop()

    # Feed one 512-sample window at a time. Feeding larger blocks makes Silero return a few
    # giant segments instead of many short ones, which then blows up memory in the decoder.
    for i in range(0, len(audio) - VAD_WINDOW + 1, VAD_WINDOW):
        vad.accept_waveform(audio[i:i + VAD_WINDOW])
        drain()
    vad.flush()
    drain()
    return found


def plan_pieces(speech: list[tuple[int, int]], total: int) -> list[tuple[int, int]]:
    """Join speech ranges with short gaps (up to CHUNK_MAX_S), then pad each side into the silence."""
    sr = SAMPLE_RATE
    merged: list[list[int]] = []
    for s, e in sorted(speech):
        if merged and s - merged[-1][1] <= JOIN_GAP_S * sr and e - merged[-1][0] <= CHUNK_MAX_S * sr:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    pieces = []
    for i, (s, e) in enumerate(merged):
        prev_end = merged[i - 1][1] if i else 0
        next_start = merged[i + 1][0] if i + 1 < len(merged) else total
        left = int(min(PAD_S * sr, max(0, s - prev_end) / 2))
        right = int(min(PAD_S * sr, max(0, next_start - e) / 2))
        pieces.append((max(0, s - left), min(total, e + right)))
    return pieces


def result_words(res, offset: float) -> list[dict]:
    """Model tokens -> words. A token starting with a space starts a new word; others attach."""
    toks = list(res.tokens)
    stamps = list(res.timestamps)
    durs = list(res.durations) if len(res.durations) == len(toks) else [0.0] * len(toks)
    logps = list(res.ys_log_probs) if len(res.ys_log_probs) == len(toks) else [0.0] * len(toks)
    words: list[dict] = []
    for tok, t, d, lp in zip(toks, stamps, durs, logps):
        start, end = offset + t, offset + t + max(0.0, d)
        if tok.startswith(" ") or not words:
            text = tok.strip()
            if text:
                words.append({"w": text, "s": start, "e": end, "lp": lp})
        else:
            w = words[-1]
            w["w"] += tok
            w["e"] = max(w["e"], end)
            w["lp"] = min(w["lp"], lp)
    return words


def transcribe_samples(samples, paths: Paths, threads: int) -> tuple[list[dict], dict]:
    import numpy as np
    total = len(samples)
    audio_s = total / SAMPLE_RATE
    t0 = time.time()
    say("Finding speech...")
    speech = find_speech(samples, paths.models / VAD_FILE)
    pieces = plan_pieces(speech, total)
    if not pieces:
        raise Fail("No speech found in that recording.")
    t_vad = time.time() - t0
    say(f"  {len(pieces)} pieces of speech, {sum(e - s for s, e in pieces) / SAMPLE_RATE / 60:.1f} min "
        f"of {audio_s / 60:.1f} min ({t_vad:.0f} s)")
    say(f"Transcribing with {threads} CPU threads...")
    rec = make_recognizer(paths.models, threads)
    raw: list[dict] = []
    t1, next_report, batch = time.time(), 0.1, DECODE_BATCH
    for b in range(0, len(pieces), batch):
        group = pieces[b:b + batch]
        streams = []
        for s, e in group:
            st = rec.create_stream()
            st.accept_waveform(SAMPLE_RATE, samples[s:e].astype(np.float32) / 32768.0)
            streams.append(st)
        rec.decode_streams(streams)
        for (s, _), st in zip(group, streams):
            raw.extend(result_words(st.result, s / SAMPLE_RATE))
        done = group[-1][1] / total
        if done >= next_report or b + batch >= len(pieces):
            spent = time.time() - t1
            left = spent / max(done, 1e-6) * (1 - done)
            say(f"  {int(done * 100):3d}%  ({mmss(done * audio_s)} of {mmss(audio_s)})"
                + (f"  about {max(1, round(left / 60))} min left" if done < 1 else ""))
            next_report = done + 0.1
    words = finish_words(raw)
    asr_s = time.time() - t0
    stats = {"audio_seconds": round(audio_s, 1), "processing_seconds": round(asr_s, 1),
             "speed_x_realtime": round(audio_s / max(asr_s, 1e-6), 1), "pieces": len(pieces),
             "threads": threads}
    return words, stats


def finish_words(raw: list[dict]) -> list[dict]:
    raw.sort(key=lambda w: w["s"])
    out = []
    for i, w in enumerate(raw):
        end = max(w["e"], w["s"] + 0.08)
        if i + 1 < len(raw) and end > raw[i + 1]["s"]:
            end = max(w["s"] + 0.02, raw[i + 1]["s"])
        out.append({"w": w["w"], "s": round(w["s"], 2), "e": round(end, 2),
                    "p": round(math.exp(min(0.0, w["lp"])), 2)})
    return out


# --------------------------------------------------------------------------------------------
# Transcript files


def ends_sentence(word: str) -> bool:
    core = word.rstrip("\"')]")
    return core[-1:] in (".", "?", "!") and core.lower() not in ABBREVIATIONS


def sentences(words: list[dict], max_gap: float = 1.2, max_words: int = 60) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = nxt["s"] - w["e"] if nxt else 99.0
        if (nxt is None or gap >= max_gap or len(cur) >= max_words
                or (ends_sentence(w["w"]) and (gap > 0.25 or nxt["w"][:1].isupper()))):
            out.append({"s": words[cur[0]]["s"], "e": words[cur[-1]]["e"], "i0": cur[0], "i1": cur[-1],
                        "text": " ".join(words[k]["w"] for k in cur)})
            cur = []
    return out


def paragraphs(sents: list[dict], max_len: float = 45.0, gap: float = 1.5) -> list[list[int]]:
    out, cur = [], []
    for k, s in enumerate(sents):
        if cur and (s["s"] - sents[cur[-1]]["e"] >= gap or s["e"] - sents[cur[0]]["s"] > max_len):
            out.append(cur)
            cur = []
        cur.append(k)
    if cur:
        out.append(cur)
    return out


def srt_cues(words: list[dict], max_chars: int = 84, max_dur: float = 6.0, max_gap: float = 0.8):
    cues, cur = [], []

    def flush():
        if cur:
            text = " ".join(words[k]["w"] for k in cur)
            if len(text) > 42:  # two lines, split near the middle
                mid = len(text) // 2
                cut = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - mid), default=None)
                if cut:
                    text = text[:cut] + "\n" + text[cut + 1:]
            cues.append((words[cur[0]]["s"], words[cur[-1]]["e"], text))
            cur.clear()

    for i, w in enumerate(words):
        if cur:
            length = sum(len(words[k]["w"]) + 1 for k in cur) + len(w["w"])
            if (length > max_chars or w["e"] - words[cur[0]]["s"] > max_dur
                    or w["s"] - words[cur[-1]]["e"] > max_gap):
                flush()
        cur.append(i)
        if ends_sentence(w["w"]) and words[cur[-1]]["e"] - words[cur[0]]["s"] >= 1.0:
            flush()
    flush()
    return cues


def write_transcripts(job: Path, record: dict, words: list[dict]) -> dict:
    src = record["source"]
    sents = sentences(words)
    paras = paragraphs(sents)
    title = src.get("title") or record["name"]
    made = record["stages"]["transcribe"]
    low = sum(1 for w in words if w["p"] < LOW_CONFIDENCE)
    yt_id = src.get("id") if src.get("kind") == "youtube" else None

    write_json(job / "transcript.json", {
        "tool": TOOL_NAME, "version": VERSION, "made": made["done"], "model": ASR_LABEL,
        "title": title, "duration": src.get("duration"), "word_count": len(words),
        "fields": {"w": "word", "s": "start seconds", "e": "end seconds", "p": "model confidence 0-1"},
        "words": words,
        "sentences": sents,
    })

    head = (f"{title}\nSource: {src.get('url') or src.get('input')}\n"
            f"Length {mmss(src.get('duration') or 0)} · {len(words):,} words · transcribed "
            f"{made['done'][:16].replace('T', ' ')} by {TOOL_NAME} {VERSION} ({ASR_LABEL})\n")
    body = [f"[{hms(sents[p[0]]['s'])}] " + " ".join(sents[k]["text"] for k in p) for p in paras]
    (job / "transcript.txt").write_text(head + "\n" + "\n\n".join(body) + "\n", encoding="utf-8")

    cues = srt_cues(words)
    (job / "transcript.srt").write_text(
        "\n".join(f"{n}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n" for n, (s, e, t) in enumerate(cues, 1)),
        encoding="utf-8")

    def word_html(w):
        t = html.escape(w["w"])
        return f'<u class="low" title="unsure ({int(w["p"] * 100)}%)">{t}</u>' if w["p"] < LOW_CONFIDENCE else t

    rows = []
    for p in paras:
        s0 = sents[p[0]]["s"]
        stamp = (f'<a class="t" href="https://youtu.be/{yt_id}?t={int(s0)}">{hms(s0)}</a>' if yt_id
                 else f'<span class="t">{hms(s0)}</span>')
        text = " ".join(word_html(words[i]) for k in p for i in range(sents[k]["i0"], sents[k]["i1"] + 1))
        rows.append(f'<p id="t{int(s0)}">{stamp}{text}</p>')
    where = html.escape(src.get("url") or src.get("input") or "")
    link = f'<a href="{where}">{where}</a>' if src.get("url") else where
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transcript · {html.escape(title)}</title>
<style>
:root{{--bg:#fafafa;--fg:#1a1a1a;--muted:#666;--box:#fff7e0;--boxline:#e6c65c;--link:#1d5fa8;--low:#c0392b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#131313;--fg:#e8e8e8;--muted:#9a9a9a;--box:#2a2410;--boxline:#7a6526;--link:#86b6ff;--low:#ff8a7a}}}}
body{{font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:auto;padding:16px;color:var(--fg);background:var(--bg)}}
h1{{font-size:21px;margin:0 0 4px}}.meta{{color:var(--muted);font-size:13px;margin-bottom:12px;overflow-wrap:anywhere}}
.box{{background:var(--box);border:1px solid var(--boxline);border-radius:8px;padding:10px 12px;margin:10px 0 18px;font-size:15px}}
p{{margin:0 0 14px}}.t{{font:13px ui-monospace,Menlo,monospace;color:var(--link);text-decoration:none;margin-right:8px}}
u.low{{text-decoration:underline dotted var(--low);text-decoration-thickness:2px;text-underline-offset:3px}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="meta">{link}<br>Length {mmss(src.get('duration') or 0)} · {len(words):,} words · transcribed {made['done'][:16].replace('T', ' ')} · {made['speed_x_realtime']}× real time</div>
<div class="box"><b>Read first</b><br>
Machine transcript ({html.escape(ASR_LABEL)}), made on the computer that ran it. Nothing was uploaded.<br>
{'Tap a timestamp to open the video at that spot.<br>' if yt_id else ''}
<u class="low">Dotted red underline</u> = the model was unsure ({low:,} words). Names and numbers there need a check before they go in a caption.<br>
No speaker names yet. Paragraphs break at pauses or every ~45 s, not at speaker changes.</div>
{chr(10).join(rows)}
</body></html>
"""
    (job / "transcript.html").write_text(page, encoding="utf-8")
    return {"sentences": len(sents), "paragraphs": len(paras), "cues": len(cues), "low_confidence": low}


# --------------------------------------------------------------------------------------------
# Commands


def cmd_transcribe(args, paths: Paths) -> int:
    ffmpeg, ffprobe = need_tool("ffmpeg"), need_tool("ffprobe")
    try:
        import numpy  # noqa: F401
        import sherpa_onnx  # noqa: F401
    except ImportError:
        raise Fail("The speech engine (sherpa-onnx) is not installed. Run the installer (install_mac.sh).")
    ensure_models(paths.models)

    kind, key = source_key(args.source)
    job = find_job(paths.jobs, key)
    record = json.loads((job / "job.json").read_text()) if job else None

    if record and Path(record["source"].get("media", "")).exists():
        media = Path(record["source"]["media"])
        say(f"Using the job already started: {job.name}")
    elif kind == "file":
        media = Path(args.source).expanduser().resolve()
        if not media.exists():
            raise Fail(f"File not found: {media}")
    else:
        media, link_meta = download_link(args.source, paths, ffmpeg)

    if record is not None and record["source"].get("media") != str(media):
        record["source"]["media"] = str(media)  # the file was downloaded again or moved
        write_json(job / "job.json", record)
    if record is None:
        src = {"kind": kind, "key": key, "input": args.source}
        if kind != "file":
            src.update(link_meta)
        src["media"] = str(media)
        src.update(probe(ffprobe, media))
        name = args.name or src.get("title") or media.stem
        job = new_job_dir(paths.jobs, name)
        record = {"tool": TOOL_NAME, "version": VERSION, "name": job.name, "created": now_local(),
                  "source": src, "work": str(paths.work / job.name), "stages": {}}
        write_json(job / "job.json", record)
        say(f"New job: {job}")
    src = record["source"]
    say(f"  {media.name}  ·  {mmss(src['duration'])}  ·  "
        + (f"video {src.get('width')}x{src.get('height')} @ {src.get('fps')} fps" if src.get("has_video")
           else "audio only (Stage 3 will make an audiogram)"))

    if (job / "transcript.json").exists() and not args.force:
        say(f"\nAlready transcribed. Open: {job / 'transcript.html'}\n(Use --force to redo it.)")
        return 0

    work = Path(record["work"])
    audio = work / "audio16k.wav"
    if not audio.exists():
        say("Pulling out the audio...")
        extract_audio(ffmpeg, media, audio)

    words, stats = transcribe_samples(load_wav(audio), paths, args.threads)
    record["stages"]["transcribe"] = {"done": now_local(), "model": ASR_LABEL, "words": len(words),
                                      "tool_version": VERSION, **stats}
    counts = write_transcripts(job, record, words)
    record["stages"]["transcribe"].update(counts)
    write_json(job / "job.json", record)

    say(f"\nDone: {len(words):,} words, {counts['low_confidence']:,} marked unsure. "
        f"Took {mmss(stats['processing_seconds'])} for {mmss(stats['audio_seconds'])} "
        f"({stats['speed_x_realtime']}x real time).")
    say(f"  Read it:  {job / 'transcript.html'}")
    say(f"  Also:     transcript.txt (plain), transcript.srt (subtitles), transcript.json (word times)")
    return 0


def cmd_setup(args, paths: Paths) -> int:
    ensure_models(paths.models)
    return cmd_doctor(args, paths)


def cmd_doctor(args, paths: Paths) -> int:
    problems = 0

    def line(ok, label, detail="", fix=""):
        nonlocal problems
        problems += 0 if ok else 1
        say(f"  {'OK ' if ok else 'FIX'}  {label}{': ' + detail if detail else ''}")
        if not ok and fix:
            say(f"        → {fix}")

    say(f"{TOOL_NAME} {VERSION}  ({TOOL_DIR})")
    line(sys.version_info >= (3, 9), "Python", sys.version.split()[0], "Python 3.9 or newer is needed")
    for name in ("ffmpeg", "ffprobe"):
        p = find_tool(name)
        ver = run([p, "-version"]).stdout.splitlines()[0][:60] if p else ""
        line(bool(p), name, f"{p}  ({ver})" if p else "missing", "brew install ffmpeg-full")
    ff = find_tool("ffmpeg")
    if ff:
        has_subs = " subtitles " in subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True,
                                                     text=True).stdout
        line(has_subs, "ffmpeg caption filter (libass, for Stage 3)", "present" if has_subs else "missing",
             "brew install ffmpeg-full  (Homebrew's plain ffmpeg has no libass)")
    try:
        import sherpa_onnx
        line(True, "sherpa-onnx speech engine", sherpa_onnx.__version__)
    except ImportError:
        line(False, "sherpa-onnx speech engine", "missing", "run install_mac.sh")
    try:
        import numpy
        line(True, "numpy", numpy.__version__)
    except ImportError:
        line(False, "numpy", "missing", "run install_mac.sh")
    try:
        import yt_dlp
        line(True, "yt-dlp (links)", yt_dlp.version.__version__)
    except ImportError:
        line(False, "yt-dlp (links)", "missing", "run install_mac.sh")
    rt = js_runtime()
    line(bool(rt), "JavaScript runtime for YouTube", next(iter(rt)) if rt else "missing", "brew install deno")
    line(models_ready(paths.models), "speech models", str(paths.models), "llclip setup")
    for label, d in (("Jobs folder (transcripts, clips)", paths.jobs), ("work folder (videos, audio)", paths.work)):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe_file = d / ".llclip-write-test"
            probe_file.write_text("ok")
            probe_file.unlink()
            free = shutil.disk_usage(d).free / 1e9
            line(free > 5, label, f"{d}  ({free:.0f} GB free)", "free up disk space (5 GB+)")
        except OSError as e:
            line(False, label, f"{d} not writable ({e})")
    if models_ready(paths.models):
        try:
            import numpy as np
            test = paths.models / ASR_DIR / "test_wavs" / "0.wav"
            if test.exists():
                t = time.time()
                rec = make_recognizer(paths.models, default_threads())
                samples = load_wav(test)
                st = rec.create_stream()
                st.accept_waveform(SAMPLE_RATE, samples.astype(np.float32) / 32768.0)
                rec.decode_stream(st)
                line(bool(st.result.text.strip()), "test transcription",
                     f"\"{st.result.text.strip()[:60]}…\" ({time.time() - t:.1f} s)")
        except Exception as e:  # report, don't crash the checkup
            line(False, "test transcription", str(e)[:200])
    say("\nAll good." if problems == 0 else f"\n{problems} thing(s) to fix (see → lines).")
    return 0 if problems == 0 else 1


def cmd_list(args, paths: Paths) -> int:
    jobs = sorted(paths.jobs.glob("*/job.json")) if paths.jobs.exists() else []
    if not jobs:
        say(f"No jobs yet in {paths.jobs}")
        return 0
    for jf in jobs:
        try:
            j = json.loads(jf.read_text())
        except (OSError, ValueError):
            continue
        stages = ", ".join(j.get("stages", {})) or "started"
        say(f"  {jf.parent.name}   {mmss(j['source'].get('duration') or 0)}   [{stages}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llclip", description=f"{TOOL_NAME} {VERSION}",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Example:  llclip transcribe \"https://youtu.be/VIDEO_ID\" --name \"Footballers Dynasty\"")
    ap.add_argument("--jobs", help="folder for transcripts and clips (default: Jobs/ next to this script)")
    ap.add_argument("--work", help="folder for downloaded videos and audio (default: ~/Documents/Clipping Service Footage/Clipping Tool Work)")
    ap.add_argument("--models", help="folder for speech models (default: ~/Documents/Clipping Service Footage/_engine models)")
    ap.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="Stage 1: get a video (file or link) and transcribe it")
    t.add_argument("source", help="a video/audio file, or a link (YouTube or anything yt-dlp can open)")
    t.add_argument("--name", help="job name, e.g. \"Footballers Dynasty 11-14\" (default: the video title)")
    t.add_argument("--force", action="store_true", help="transcribe again even if a transcript exists")
    t.add_argument("--threads", type=int, default=default_threads(), help="CPU threads (default: %(default)s)")
    t.set_defaults(func=cmd_transcribe)

    sub.add_parser("setup", help="download the speech models and run the checkup").set_defaults(func=cmd_setup)
    sub.add_parser("doctor", help="check the install").set_defaults(func=cmd_doctor)
    sub.add_parser("list", help="show jobs").set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    paths = resolve_paths(args)
    try:
        return args.func(args, paths)
    except Fail as e:
        print(f"\nStopped: {e}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("\nStopped (Ctrl-C). Run the same command again to pick up where it left off.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
