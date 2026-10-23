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
    # Where attachments are stored when Google Drive isn't used. Point it inside iCloud Drive on a Mac
    # (~/Library/Mobile Documents/com~apple~CloudDocs/Family Hub) to see them in the Files app on iPhones.
    files_dir: str = field(default_factory=lambda: _env("FAMILY_HUB_FILES_DIR", "./files"))
    # Copy the database here once a day (keeps 14). Blank = no backups.
    backup_dir: str = field(default_factory=lambda: _env("FAMILY_HUB_BACKUP_DIR"))
    session_days: int = field(default_factory=lambda: int(_env("FAMILY_HUB_SESSION_DAYS", "30")))

    # Optional: finish Google sign-in at http://localhost:8000 on the hub's own computer instead.
    google_redirect_base: str = field(default_factory=lambda: _env("FAMILY_HUB_GOOGLE_REDIRECT_BASE").rstrip("/"))

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.google_redirect_base or self.base_url}/api/integrations/google/callback"


settings = Settings()
