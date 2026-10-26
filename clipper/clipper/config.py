"""Config loading: config.yaml for settings, .env for secrets, both in the data dir."""
from __future__ import annotations

import os
import re
import shutil
from datetime import time
from functools import cached_property
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

Platform = Literal["youtube", "tiktok", "instagram"]
PLATFORMS: tuple[str, ...] = ("youtube", "tiktok", "instagram")

# tier -> (model, effort). "free" never calls an API.
AI_TIERS: dict[str, tuple[str, str] | None] = {
    "free": None,
    "budget": ("claude-haiku-5-5", "medium"),
    "standard": ("claude-sonnet-5-5", "medium"),
    "premium": ("claude-opus-5-5", "high"),
}

EXAMPLE_CONFIG = Path(__file__).with_name("config.example.yaml")

DEFAULT_CAPTIONS = {
    "youtube": "{hook}\n\n{caption}\n\nFull episode: {video_url}\n{hashtags}",
    "tiktok": "{caption} Full episode on YouTube: {client_name} {hashtags}",
    "instagram": "{hook}\n\n{caption}\n\nFull episode on YouTube (link in bio) {hashtags}",
}


class Show(BaseModel):
    slug: str
    name: str
    match: list[str] = []
    hashtags: list[str] = []
    youtube: str | None = None      # set if this show gets its own channel later
    accounts: str | None = None     # post with another show's connected accounts, e.g. "main"

    @cached_property
    def patterns(self) -> list[re.Pattern]:
        return [re.compile(p, re.IGNORECASE) for p in self.match]

    def matches(self, title: str) -> bool:
        return not self.match or any(p.search(title) for p in self.patterns)

    @property
    def account_show(self) -> str:
        return self.accounts or self.slug


class Posting(BaseModel):
    timezone: str = "America/New_York"
    slots: list[str] = ["12:15", "19:30"]
    platforms: list[Platform] = list(PLATFORMS)
    youtube_privacy: Literal["public", "unlisted", "private"] = "public"

    @field_validator("slots")
    @classmethod
    def _valid_slots(cls, v: list[str]) -> list[str]:
        for s in v:
            time.fromisoformat(s)
        return sorted(v)


class Client(BaseModel):
    slug: str
    name: str
    youtube: str
    status: Literal["prospect", "client", "paused"] = "prospect"
    backfill: int = 3
    sample_videos: int = 2
    min_source_minutes: float = 8
    hashtags: list[str] = []
    captions: dict[str, str] = {}
    posting: Posting = Posting()
    shows: list[Show] = []

    @model_validator(mode="after")
    def _ensure_main(self) -> "Client":
        if not any(not s.match for s in self.shows):
            self.shows.append(Show(slug="main", name=self.name))
        return self

    def show(self, slug: str | None) -> Show:
        for s in self.shows:
            if s.slug == slug:
                return s
        return self.shows[-1]

    def classify(self, title: str) -> Show:
        for s in self.shows:
            if s.matches(title):
                return s
        return self.shows[-1]

    def caption_template(self, platform: str) -> str:
        return self.captions.get(platform) or DEFAULT_CAPTIONS[platform]

    @property
    def can_post(self) -> bool:
        return self.status == "client"


class Worker(BaseModel):
    timezone: str = "Pacific/Honolulu"
    active_hours: str = "08:00-17:00"
    active_days: list[str] = ["mon", "tue", "wed", "thu", "fri"]
    publish_anytime: bool = True
    tick_seconds: int = 300
    sync_every_minutes: int = 60
    discover_every_hours: int = 24
    max_videos_per_tick: int = 2

    @property
    def window(self) -> tuple[time, time]:
        start, end = self.active_hours.split("-")
        return time.fromisoformat(start.strip()), time.fromisoformat(end.strip())


class AI(BaseModel):
    tier: Literal["free", "budget", "standard", "premium"] = "budget"
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    monthly_budget_usd: float = 5.0

    def resolved(self) -> tuple[str, str] | None:
        base = AI_TIERS[self.tier]
        if base is None:
            return None
        return self.model or base[0], self.effort or base[1]


class Transcribe(BaseModel):
    engine: Literal["auto", "youtube", "whisper"] = "auto"
    whisper_model: str = "small"
    language: str = "en"


class Clips(BaseModel):
    per_video: int = 4
    min_seconds: float = 20
    max_seconds: float = 75
    montage: bool = True
    montage_max_seconds: float = 45
    skip_intro_seconds: float = 45
    skip_outro_seconds: float = 60


class Render(BaseModel):
    layout: Literal["blur", "crop"] = "blur"
    crop_x: float = Field(0.5, ge=0, le=1)
    width: int = 1080
    height: int = 1920
    fps: int = 30
    font: str = "Montserrat ExtraBold"
    font_size: int = 84
    uppercase: bool = True
    words_per_caption: int = 3
    caption_color: str = "#FFFFFF"
    highlight_color: str = "#FFB627"
    outline_color: str = "#000000"
    caption_y: float = 0.72          # vertical position of captions, fraction of height
    hook_seconds: float = 0
    cta: str = "FULL EPISODE ON YOUTUBE"
    cta_mode: Literal["end", "always", "off"] = "end"
    cta_seconds: float = 4
    encoder: Literal["auto", "libx264", "h264_videotoolbox"] = "auto"
    preset: str = "veryfast"
    crf: int = 21
    loudnorm: bool = True
    keep_source_days: int = 3


class Discovery(BaseModel):
    queries: list[str] = ["fantasy football podcast"]
    results_per_query: int = 30
    max_new_channels: int = 40
    min_long_videos: int = 50
    max_shorts: int = 0
    min_subscribers: int = 2000
    max_subscribers: int = 750_000
    active_within_days: int = 30
    check_socials: bool = True
    min_score: float = 60


class Publish(BaseModel):
    public_base_url: str = ""
    retry_limit: int = 3
    tiktok_mode: Literal["direct", "inbox"] = "direct"
    instagram_upload: Literal["resumable", "url"] = "resumable"
    graph_api_version: str = "v23.0"


class Config(BaseModel):
    data_dir: Path
    worker: Worker = Worker()
    ai: AI = AI()
    transcribe: Transcribe = Transcribe()
    clips: Clips = Clips()
    render: Render = Render()
    discovery: Discovery = Discovery()
    publish: Publish = Publish()
    clients: list[Client] = []

    def client(self, slug: str) -> Client:
        for c in self.clients:
            if c.slug == slug:
                return c
        raise KeyError(f"no client {slug!r} in config.yaml")

    @property
    def public_base_url(self) -> str:
        return (os.environ.get("PUBLIC_BASE_URL") or self.publish.public_base_url).rstrip("/")

    def path(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.path("clipper.db")

    def dir(self, name: str) -> Path:
        p = self.data_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p


def default_data_dir() -> Path:
    return Path(os.environ.get("CLIPPER_DATA", "~/sandbox-data/clipper")).expanduser()


def load_env_file(path: Path) -> None:
    """Minimal .env loader. Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def init_data_dir(data_dir: Path | None = None) -> Path:
    data_dir = data_dir or default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = data_dir / "config.yaml"
    if not cfg.exists():
        shutil.copy(EXAMPLE_CONFIG, cfg)
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    data_dir = default_data_dir()
    path = Path(path or os.environ.get("CLIPPER_CONFIG") or data_dir / "config.yaml").expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `clipper init` (or `sandbox.sh setup`) first.")
    load_env_file(path.parent / ".env")
    raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("data_dir", str(data_dir))
    raw["data_dir"] = str(Path(raw["data_dir"]).expanduser())
    return Config.model_validate(raw)
