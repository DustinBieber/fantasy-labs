# Logic Lab Clipping Tool

Give it a long video (a file or a YouTube link). It transcribes the video, picks the
moments worth clipping, cuts them vertical, burns in captions, and drops finished clips
in an output folder. Everything runs on your Mac — no API key, nothing uploaded.

> **Built in three stages.** Stage 1 (transcribe) is working now. Stage 2 (pick moments)
> and Stage 3 (cut and caption) come next, each after your check. See
> [Status](#status) at the bottom.

This is a fresh, self-contained tool. It does **not** replace the `clipper/` service in
this repo — that one is the automated creator-outreach pipeline. This is the simple
"one video in, clips out" tool you asked for.

---

## Install (one time, about 10 minutes)

On the Mac, open Terminal and run (use the real path to this folder):

```bash
bash "/path/to/Logic Lab/Clipping Tool/install_mac.sh"
```

It installs what's needed with Homebrew (ffmpeg-full, Python, Deno), sets up the speech
engine, downloads the speech model (~480 MB, once), and adds an `llclip` command. Then
open a **new** Terminal window.

If you don't have Homebrew yet, get it from <https://brew.sh> first.

### Check it's healthy any time

```bash
llclip doctor
```

It lists each part as `OK` or `FIX`, with the exact fix on the next line.

---

## Stage 1: transcribe

```bash
# From a file you already have:
llclip transcribe ~/Downloads/episode.mp4 --name "Footballers Dynasty 11-14"

# From a YouTube link (or anything yt-dlp can open):
llclip transcribe "https://www.youtube.com/watch?v=VIDEO_ID"
```

It makes a job folder under `Jobs/` next to the tool and writes:

| File | What it is |
|---|---|
| `transcript.html` | **Open this one.** The whole transcript, timestamped. Words the model was unsure of are underlined in red — those are the names and numbers to double-check before they go in a caption. On a YouTube job, each timestamp links to that spot in the video. |
| `transcript.txt` | Plain text, timestamped by paragraph. |
| `transcript.srt` | Standard subtitle file. |
| `transcript.json` | Every word with its start/end time and confidence. Stage 2 reads this. |
| `job.json` | What the job is and what's been done so far. |

See everything so far:

```bash
llclip list
```

### Where files go

- **`Jobs/<date> <name>/`** — next to the tool (so it can sit in Google Drive): the
  transcript, and later the moment list and finished clips. These are small.
- **`~/Documents/Clipping Service Footage/Clipping Tool Work/`** — the downloaded video
  and the extracted audio. These are big and stay on the Mac (no backup), matching how
  the rest of Logic Lab keeps footage local.
- **`~/Documents/Clipping Service Footage/_engine models/`** — the speech model, shared
  with the rest of the Logic Lab engine.

Point any of these somewhere else with `--jobs`, `--work`, `--models`, or the
`LLCLIP_JOBS` / `LLCLIP_WORK` / `LLCLIP_MODELS` environment variables.

---

## What's under the hood

- **Transcription:** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) running NVIDIA's
  **Parakeet TDT 0.6B v2** English model, on the CPU. It's one of the most accurate open
  English models and needs no key. Long audio is split at silences into short pieces (the
  model needs that), transcribed, and stitched back with per-word timings.
- **Downloads:** `yt-dlp` (needs Deno, which the installer adds) for YouTube and most
  other sites. If a link won't download, download it yourself and point the tool at the
  file.
- **Captions later:** Stage 3 will use `ffmpeg-full`, which includes `libass` for
  burned-in captions. Homebrew's plain `ffmpeg` does not, which is why the installer uses
  `ffmpeg-full`.

If YouTube downloads start failing, re-run the installer to update `yt-dlp` (sites change
often).

---

## Status

- **Stage 1 — transcribe:** done. Tested end to end on a 55-minute recording.
- **Stage 2 — pick moments:** not built yet. Will read `transcript.json`, choose the
  moments worth clipping, and write a list with timestamps and a reason for each, for your
  review.
- **Stage 3 — cut and caption:** not built yet. Will cut one chosen moment to vertical
  9:16, burn in captions, and export a finished clip.

Run `llclip` with no arguments to see all commands.
