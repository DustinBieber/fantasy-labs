"""Location connectors that read where family phones are from services the family already uses.

Neither Apple Find My nor Google Maps location sharing has a public API. Both connectors use the same
unofficial web endpoints that Home Assistant's long-standing integrations rely on (pyicloud and
locationsharinglib). They can break if Apple/Google change their websites; if that happens the hub
shows the error on the Connections page and Wi-Fi presence / Shortcuts keep working.
"""
import logging

from ..models import Member
from ..services import geo
from ..timeutil import utcnow
from .base import Connector, Ctx, Field, NeedsInput, Status

log = logging.getLogger(__name__)


def apply_locations(ctx: Ctx, found: list[dict]) -> str:
    """found: [{id, name, lat, lng, accuracy, battery, ts}] → pings for subjects mapped to members.

    New subjects are auto-matched to a family member when the member's name appears in the device or
    person name (e.g. "Sam's iPhone" → Sam). Parents can change the mapping on the Connections page.
    """
    opts = ctx.options()
    mapping = {str(k): v for k, v in (opts.get("map") or {}).items()}
    last_ts = dict(opts.get("last_ts") or {})
    members = ctx.db.query(Member).filter_by(family_id=ctx.family.id).all()
    for s in found:
        sid = str(s["id"])
        if sid not in mapping:
            guess = next((m.id for m in members if m.name.lower() in (s["name"] or "").lower()), None)
            mapping[sid] = guess
    recorded = 0
    for s in found:
        sid, member_id = str(s["id"]), mapping.get(str(s["id"]))
        if not member_id or s.get("lat") is None:
            continue
        if s.get("ts") and last_ts.get(sid) == s["ts"]:
            continue  # nothing new since the last poll
        member = ctx.db.get(Member, member_id)
        if member is None or member.family_id != ctx.family.id or not member.share_location:
            continue
        geo.record_ping(ctx.db, ctx.family, member, s["lat"], s["lng"], s.get("accuracy"), s.get("battery"))
        last_ts[sid] = s.get("ts")
        recorded += 1
    subjects = [{"id": str(s["id"]), "name": s["name"], "has_location": s.get("lat") is not None} for s in found]
    integ = ctx.save(options={"subjects": subjects, "map": mapping, "last_ts": last_ts})
    integ.last_sync_at = utcnow()
    ctx.db.commit()
    return f"{len(found)} found, {recorded} updated"


# ---------------------------------------------------------------- Apple Find My

_pending: dict[int, object] = {}   # family id → PyiCloudService waiting for a 2FA code
_sessions: dict[int, object] = {}  # family id → signed-in PyiCloudService


class FindMy(Connector):
    key = "findmy"
    name = "Apple Find My (iPhone locations)"
    category = "location"
    summary = ("Live locations of every iPhone, iPad and Mac in your Apple Family Sharing group, "
               "the same data as the Find My app. No app on the phones.")
    fields = [
        Field("apple_id", "Apple ID (the family organizer is best)", "email"),
        Field("password", "Apple ID password", "password", secret=True,
              help="Find My needs your real Apple ID password (app-specific passwords don't work for it). "
                   "It's stored encrypted on your Mac."),
    ]
    steps = [
        "Make sure each person has Share My Location on (iPhone Settings → [name] → Find My) and is in your Family Sharing group.",
        "Enter the Apple ID and password below, then the 6-digit code Apple sends to your devices.",
        "Match each device to a person below. The hub checks every 10 minutes.",
    ]
    poll_minutes = 10
    docs_url = "https://support.apple.com/en-us/104978"

    def _service(self, apple_id: str, password: str, ctx: Ctx):
        from pyicloud import PyiCloudService

        return PyiCloudService(apple_id, password, cookie_directory=str(ctx.state_path("cookies").parent),
                               with_family=True, refresh_interval=None)

    def connect(self, ctx: Ctx, values: dict) -> str:
        api = self._service(values["apple_id"], values["password"], ctx)
        ctx.save(account=values["apple_id"], secret={"apple_id": values["apple_id"], "password": values["password"]})
        if getattr(api, "requires_2fa", False) or getattr(api, "requires_2sa", False):
            _pending[ctx.family.id] = api
            raise NeedsInput("code", "Enter the 6-digit code Apple just sent to your iPhone, iPad or Mac.",
                             [Field("code", "Verification code", "text", placeholder="123456")])
        _sessions[ctx.family.id] = api
        return self.poll(ctx)

    def finish(self, ctx: Ctx, values: dict) -> str:
        api = _pending.pop(ctx.family.id, None)
        if api is None:
            raise ValueError("The sign-in expired. Start again.")
        if not api.validate_2fa_code(str(values.get("code", "")).strip()):
            _pending[ctx.family.id] = api
            raise ValueError("That code didn't work. Check it and try again.")
        if not api.is_trusted_session:
            api.trust_session()  # remembers this Mac so codes aren't needed on every restart
        _sessions[ctx.family.id] = api
        return self.poll(ctx)

    def poll(self, ctx: Ctx) -> str:
        api = _sessions.get(ctx.family.id)
        if api is None:
            secret = ctx.secret()
            api = self._service(secret["apple_id"], secret["password"], ctx)
            if getattr(api, "requires_2fa", False):
                raise RuntimeError("Apple needs a new verification code. Reconnect Find My on the Connections page.")
            _sessions[ctx.family.id] = api
        try:
            devices = api.devices
            devices.refresh(locate=True)
        except Exception:
            _sessions.pop(ctx.family.id, None)  # session expired; rebuild next time
            raise
        found = []
        for dev in devices:
            d = dev.data
            loc = d.get("location") or {}
            battery = d.get("batteryLevel")
            found.append({
                "id": d.get("id"), "name": d.get("name") or d.get("deviceDisplayName") or "Device",
                "lat": loc.get("latitude"), "lng": loc.get("longitude"), "accuracy": loc.get("horizontalAccuracy"),
                "battery": round(battery * 100) if isinstance(battery, (int, float)) and battery <= 1 else battery,
                "ts": loc.get("timeStamp"),
            })
        return apply_locations(ctx, found)

    def disconnect(self, ctx: Ctx) -> None:
        _sessions.pop(ctx.family.id, None)
        _pending.pop(ctx.family.id, None)
        folder = ctx.state_path("cookies").parent
        for f in folder.glob("*"):
            f.unlink(missing_ok=True)
        super().disconnect(ctx)


# ---------------------------------------------------------------- Google Maps location sharing

class GoogleLocationSharing(Connector):
    key = "google_location"
    name = "Google Maps location sharing (Android)"
    category = "location"
    summary = ("Reads locations that family members share with you in Google Maps, which works for "
               "Android phones. Nothing new to install on the phones.")
    fields = [
        Field("account", "Your Google account (the one people share with)", "email"),
        Field("cookies", "Google cookies (cookies.txt)", "textarea", secret=True,
              help="Google has no sign-in for this, so the hub reuses your browser's Google session: "
                   "sign in at google.com/maps in a private window, export cookies for google.com with a "
                   "'cookies.txt' exporter, paste the file here, then close that window without signing out."),
    ]
    steps = [
        "On each Android phone: Google Maps → profile picture → Location sharing → Share with your Google account → Until you turn this off.",
        "Paste your Google cookies below (see the help text).",
        "Match each shared person to a family member. The hub checks every 5 minutes.",
    ]
    poll_minutes = 5

    def _service(self, ctx: Ctx):
        from locationsharinglib import Service

        path = ctx.state_path("cookies.txt")
        path.write_text(ctx.secret()["cookies"])
        path.chmod(0o600)
        return Service(cookies_file=str(path), authenticating_account=ctx.secret()["account"])

    def connect(self, ctx: Ctx, values: dict) -> str:
        if "# Netscape HTTP Cookie File" not in values["cookies"] and "\t" not in values["cookies"]:
            raise ValueError("That doesn't look like a cookies.txt file (it should have tab-separated lines).")
        ctx.save(account=values["account"], secret={"account": values["account"], "cookies": values["cookies"]})
        return self.poll(ctx)

    def poll(self, ctx: Ctx) -> str:
        service = self._service(ctx)
        found = []
        for p in service.get_all_people():
            ts = p.timestamp
            found.append({"id": p.id, "name": p.full_name or p.nickname or "Person", "lat": p.latitude,
                          "lng": p.longitude, "accuracy": p.accuracy, "battery": p.battery_level,
                          "ts": int(ts) if ts else None})
        return apply_locations(ctx, found)


class WifiPresence(Connector):
    key = "wifi_presence"
    name = "Home Wi-Fi presence"
    category = "location"
    availability = "mac"
    summary = "The Mac notices family phones on your home Wi-Fi: who's home, who left. Works for iPhone and Android."
    manage = "#map"

    def status(self, ctx: Ctx) -> Status:
        if not (ctx.family.settings or {}).get("wifi_presence"):
            return Status("off")
        from ..services import presence

        n = ctx.db.query(Member).filter(Member.family_id == ctx.family.id).all()
        with_phone = sum(1 for m in n if m.wifi_macs)
        if presence.status.get("error"):
            return Status("attention", presence.status["error"])
        return Status("connected" if with_phone else "attention",
                      f"{with_phone} of {len(n)} people have a phone added")


class Shortcuts(Connector):
    key = "ios_shortcuts"
    name = "iPhone Shortcuts arrive/leave"
    category = "location"
    summary = "Built-in iPhone automations tell the hub when someone arrives at or leaves a place, anywhere."
    manage = "#map"

    def status(self, ctx: Ctx) -> Status:
        from ..models import Alert

        used = ctx.db.query(Alert).filter(Alert.family_id == ctx.family.id, Alert.kind == "location",
                                          Alert.body.like("%iPhone Shortcut%")).first()
        return Status("connected", "Receiving events") if used else Status("off", "Set up on the Map tab")
