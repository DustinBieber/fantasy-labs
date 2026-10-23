"""Runtime settings, read once from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("FAMILY_HUB_DATABASE_URL", "sqlite:///./family_hub.db"))
    # Public base URL of this server; used to build OAuth redirect URIs and webhook URLs.
    base_url: str = field(default_factory=lambda: _env("FAMILY_HUB_BASE_URL", "http://localhost:8000").rstrip("/"))
    # Fernet key used to encrypt OAuth tokens and integration secrets at rest.
    secret_key: str = field(default_factory=lambda: _env("FAMILY_HUB_SECRET_KEY"))
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    scheduler_enabled: bool = field(default_factory=lambda: _env("FAMILY_HUB_SCHEDULER", "1") == "1")
    scheduler_interval_s: int = field(default_factory=lambda: int(_env("FAMILY_HUB_SCHEDULER_INTERVAL", "60")))
    session_days: int = field(default_factory=lambda: int(_env("FAMILY_HUB_SESSION_DAYS", "30")))

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url}/api/integrations/google/callback"


settings = Settings()
