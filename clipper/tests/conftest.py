import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from clipper.config import EXAMPLE_CONFIG, load_config
from clipper.db import DB
from clipper.transcripts import Transcript, Word

FIXTURES = Path(__file__).parent / "fixtures"

HOT_TAKE = (
    "here's my hot take bijan robinson is a league winner and if you fade him you are wrong "
    "trust me he is a top five running back mark my words this is the breakout"
).split()
SPONSOR = "this episode is brought to you by our sponsor use promo code footballers for a deposit match".split()
FILLER = "so yeah we were talking about the schedule and the weather and things in general today".split()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DASHBOARD_PASSWORD", "PUBLIC_BASE_URL",
                "CLIPPER_SECRET_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    raw["data_dir"] = str(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path))
    return load_config(path)


@pytest.fixture
def db(cfg):
    return DB(cfg.db_path)


def make_transcript(minutes: float = 10, hot_at: float = 300, sponsor_at: float = 120) -> Transcript:
    """Filler talk at a steady pace, one hot-take passage, one sponsor read."""
    words, t = [], 0.0
    end = minutes * 60
    while t < end:
        if hot_at <= t < hot_at + 1:
            phrase, step = HOT_TAKE * 2, 0.30
        elif sponsor_at <= t < sponsor_at + 1:
            phrase, step = SPONSOR * 2, 0.32
        else:
            phrase, step = FILLER, 0.40
        for w in phrase:
            words.append(Word(w, round(t, 2), round(t + step - 0.05, 2)))
            t += step
        t += 0.8   # pause between sentences
    return Transcript(source="test", words=words, markers=[(hot_at + 5, "[Laughter]")])


@pytest.fixture
def transcript():
    return make_transcript()


def make_video(path: Path, seconds: int = 40) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
