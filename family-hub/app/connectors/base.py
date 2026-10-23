"""Connector framework: every outside service the hub talks to is a Connector.

A connector declares what it is (category, fields to fill in, setup steps, whether it needs the Mac),
and optionally implements:
  connect(ctx, values)  validate + save credentials; may ask for a follow-up step (e.g. a 2FA code)
  finish(ctx, values)   complete that follow-up step
  poll(ctx)             pull data on a schedule (every `poll_minutes`)
  status(ctx)           report connected / needs attention / not connected

Services with no public API are still listed, as "bridge" connectors: they explain the working route
today (another connector, a webhook, or Home Assistant) so the family sees one complete checklist,
and a real implementation can drop in later without touching the rest of the app.
"""
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_json, encrypt_json
from ..models import Family, Integration
from ..timeutil import iso

CATEGORIES = {
    "calendar": "Calendars",
    "location": "Location",
    "messaging": "Notifications & messages",
    "email": "Email",
    "files": "Files",
    "home": "Doors & home",
}


@dataclass
class Field:
    name: str
    label: str
    kind: str = "text"  # text | password | textarea | email | url | number
    required: bool = True
    secret: bool = False
    help: str = ""
    placeholder: str = ""


@dataclass
class Status:
    state: str  # connected | attention | off | unavailable | needs_mac
    detail: str = ""


class NeedsInput(Exception):
    """Raised by connect() when a second step is required (e.g. a 2FA code)."""

    def __init__(self, step: str, message: str, fields: list[Field]):
        super().__init__(message)
        self.step, self.message, self.fields = step, message, fields


class Ctx:
    """What a connector gets to work with: the DB, the family, and its saved Integration row."""

    def __init__(self, db: Session, family: Family, key: str):
        self.db, self.family, self.key = db, family, key

    @property
    def integration(self) -> Integration | None:
        return self.db.query(Integration).filter_by(family_id=self.family.id, provider=self.key).first()

    def secret(self) -> dict:
        integ = self.integration
        return decrypt_json(integ.secret_enc) if integ else {}

    def options(self) -> dict:
        integ = self.integration
        return dict(integ.options or {}) if integ else {}

    def save(self, *, account: str = "", secret: dict | None = None, options: dict | None = None) -> Integration:
        integ = self.integration
        if integ is None:
            integ = Integration(family_id=self.family.id, provider=self.key, secret_enc=encrypt_json({}), options={})
            self.db.add(integ)
        if account:
            integ.account = account
        if secret is not None:
            integ.secret_enc = encrypt_json(secret)
        if options is not None:
            integ.options = {**(integ.options or {}), **options}
        integ.last_error = ""
        self.db.commit()
        return integ

    def state_path(self, name: str) -> Path:
        """A private per-family folder for session files (never inside iCloud/Drive sync folders)."""
        path = Path(settings.state_dir).expanduser().resolve() / f"family-{self.family.id}" / self.key / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class Connector:
    key = ""
    name = ""
    category = "calendar"
    summary = ""
    # ready: works anywhere · mac: needs the hub on a Mac · bridge: no public API, works through another route
    # unavailable: not possible for this account type today
    availability = "ready"
    fields: list[Field] = []
    steps: list[str] = []
    poll_minutes: int | None = None
    # Built-in connectors are configured elsewhere in the app; `manage` says where (a hash route).
    manage: str = ""
    docs_url: str = ""

    def connect(self, ctx: Ctx, values: dict) -> str:
        raise NotImplementedError

    def finish(self, ctx: Ctx, values: dict) -> str:
        raise NotImplementedError

    def poll(self, ctx: Ctx) -> str:
        return ""

    def status(self, ctx: Ctx) -> Status:
        integ = ctx.integration
        if integ is None:
            return Status("off")
        if integ.last_error:
            return Status("attention", integ.last_error)
        return Status("connected", integ.account)

    def disconnect(self, ctx: Ctx) -> None:
        integ = ctx.integration
        if integ:
            ctx.db.delete(integ)
            ctx.db.commit()

    def describe(self, ctx: Ctx) -> dict:
        st = self.status(ctx)
        integ = ctx.integration
        opts = dict(integ.options or {}) if integ else {}
        return {
            "key": self.key, "name": self.name, "category": self.category, "summary": self.summary,
            "availability": self.availability, "fields": [asdict(f) for f in self.fields], "steps": self.steps,
            "manage": self.manage, "docs_url": self.docs_url, "connectable": type(self).connect is not Connector.connect,
            "state": st.state, "detail": st.detail,
            "last_sync_at": iso(integ.last_sync_at) if integ else None,
            "subjects": opts.get("subjects", []), "mapping": opts.get("map", {}),
            "options": {k: v for k, v in opts.items() if k not in ("subjects", "map", "last_ts")},
        }
