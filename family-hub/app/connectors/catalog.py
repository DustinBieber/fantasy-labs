"""Messaging, email, files and home connectors, plus the full catalog in display order."""
from ..config import settings
from ..crypto import encrypt_json
from ..models import Integration, Lock, NotificationChannel, PushSubscription
from ..services import http
from ..services.notify import send
from .base import Connector, Ctx, Field, Status
from .calendars import GoogleCalendar, HubFeed, ICloudCalendar, ICSFeeds
from .location import FindMy, GoogleLocationSharing, Shortcuts, WifiPresence


def _channels(ctx: Ctx, kind: str) -> list[NotificationChannel]:
    return ctx.db.query(NotificationChannel).filter_by(family_id=ctx.family.id, kind=kind).all()


def channel_status(ctx: Ctx, chans: list[NotificationChannel]) -> Status:
    """Connected if an enabled channel exists and its most recent delivery (if any) succeeded."""
    from ..models import Alert

    on = [c for c in chans if c.enabled]
    if not chans:
        return Status("off")
    if not on:
        return Status("attention", "Paused")
    recent = ctx.db.query(Alert).filter_by(family_id=ctx.family.id).order_by(Alert.id.desc()).limit(50).all()
    for ch in on:
        last = next((a.delivery[str(ch.id)] for a in recent if str(ch.id) in (a.delivery or {})), "sent")
        if last != "sent":
            return Status("attention", f"Last delivery failed: {last.removeprefix('error: ')}"[:200])
    return Status("connected", f"{len(on)} active")


class ChannelConnector(Connector):
    """A connector whose "connection" is a notification channel; connect() tests it and saves the channel."""
    channel_kind = ""
    channel_label = ""

    def channel_config(self, values: dict) -> dict:
        return values

    def connect(self, ctx: Ctx, values: dict) -> str:
        from ..models import Alert

        config = self.channel_config(values)
        probe = Alert(family_id=ctx.family.id, kind="system", severity="info", title="Family Hub is connected ✅",
                      body=f"{self.name} will receive your family alerts.", audience=[])
        send(ctx.db, ctx.family, self.channel_kind, config, probe)
        ch = NotificationChannel(family_id=ctx.family.id, kind=self.channel_kind, label=self.channel_label,
                                 config_enc=encrypt_json(config), min_severity="warning", kinds=[], enabled=True)
        ctx.db.add(ch)
        ctx.db.commit()
        return "Test message sent"

    def status(self, ctx: Ctx) -> Status:
        return channel_status(ctx, _channels(ctx, self.channel_kind))

    def disconnect(self, ctx: Ctx) -> None:
        for ch in _channels(ctx, self.channel_kind):
            ctx.db.delete(ch)
        ctx.db.commit()


# ---------------------------------------------------------------- notifications & messages

class WebPush(Connector):
    key = "web_push"
    name = "Phone notifications"
    category = "messaging"
    summary = "Free push alerts on iPhone (iOS 16.4+, from the Home Screen app) and Android. No extra app."
    manage = "#settings"

    def status(self, ctx: Ctx) -> Status:
        from ..models import Member

        ids = [m.id for m in ctx.db.query(Member).filter_by(family_id=ctx.family.id)]
        n = ctx.db.query(PushSubscription).filter(PushSubscription.member_id.in_(ids)).count()
        return Status("connected", f"{n} device(s)") if n else Status("off", "Turn on in Settings on each phone")


class IMessage(ChannelConnector):
    key = "imessage"
    name = "iMessage"
    category = "messaging"
    availability = "mac"
    summary = "Alerts sent as iMessages by the Mac's Messages app, to anyone with an iPhone, iPad or Mac."
    fields = [Field("to", "Send to (phone numbers or Apple ID emails, comma separated)",
                    placeholder="+15551234567, mom@icloud.com")]
    steps = ["On the Mac, open Messages and sign in with an Apple ID.",
             "Enter who should get alerts, then press Connect. A test message is sent.",
             "If the Mac asks whether python may control Messages, click OK."]
    channel_kind, channel_label = "imessage", "iMessage"

    def channel_config(self, values: dict) -> dict:
        return {"to": [x.strip() for x in values["to"].split(",") if x.strip()], "service": "auto"}

    def status(self, ctx: Ctx) -> Status:
        return channel_status(ctx, [c for c in _channels(ctx, "imessage") if c.label != GoogleMessages.channel_label])


class GoogleMessages(IMessage):
    key = "google_messages"
    name = "Google Messages (Android texts)"
    availability = "bridge"
    summary = ("Google Messages has no sending API, so the Mac sends regular text messages (SMS) that arrive "
               "in Google Messages, relayed through a family iPhone.")
    steps = ["On an iPhone signed into the same Apple ID as the Mac: Settings → Apps → Messages → "
             "Text Message Forwarding → turn on the Mac.",
             "Enter the Android phone numbers and press Connect. A test text is sent."]
    fields = [Field("to", "Android phone numbers (comma separated)", placeholder="+15551234567")]
    channel_label = "Texts (SMS)"

    def channel_config(self, values: dict) -> dict:
        return {"to": [x.strip() for x in values["to"].split(",") if x.strip()], "service": "sms"}

    def status(self, ctx: Ctx) -> Status:
        return channel_status(ctx, [c for c in _channels(ctx, "imessage") if c.label == self.channel_label])

    def disconnect(self, ctx: Ctx) -> None:
        for ch in _channels(ctx, "imessage"):
            if ch.label == self.channel_label:
                ctx.db.delete(ch)
        ctx.db.commit()


class GoogleChat(ChannelConnector):
    key = "google_chat"
    name = "Google Chat"
    category = "messaging"
    availability = "bridge"
    summary = ("Posts alerts into a Google Chat space. Google only allows this on Google Workspace accounts. "
               "On personal Gmail there's no API; push notifications and iMessage reach the same people today.")
    fields = [Field("webhook_url", "Space webhook URL (Workspace only)", "url", secret=True,
                    placeholder="https://chat.googleapis.com/v1/spaces/…")]
    steps = ["Workspace accounts: in a Chat space → Apps & integrations → Webhooks → Add, then paste the URL.",
             "Personal Gmail: not possible yet. This slot is ready if Google opens it up."]
    docs_url = "https://developers.google.com/chat/quickstart/webhooks"
    channel_kind, channel_label = "google_chat", "Google Chat"


# ---------------------------------------------------------------- email

class GmailAppPassword(ChannelConnector):
    key = "gmail_smtp"
    name = "Gmail alerts (app password)"
    category = "email"
    summary = "Email alerts and the daily digest from your Gmail. 2 minutes to set up, no Google Cloud project needed."
    fields = [
        Field("username", "Gmail address", "email"),
        Field("password", "App password", "password", secret=True, placeholder="abcd efgh ijkl mnop",
              help="Needs 2-Step Verification. Create one at myaccount.google.com/apppasswords."),
        Field("to", "Send alerts to (comma separated)", placeholder="you@gmail.com, partner@icloud.com"),
    ]
    docs_url = "https://myaccount.google.com/apppasswords"
    channel_kind, channel_label = "email", "Email (Gmail)"

    def channel_config(self, values: dict) -> dict:
        return {"host": "smtp.gmail.com", "port": 587, "username": values["username"],
                "password": values["password"].replace(" ", ""), "from": values["username"],
                "to": [x.strip() for x in values["to"].split(",") if x.strip()]}


# ---------------------------------------------------------------- files

class FileStorage(Connector):
    key = "files"
    name = "Attachment storage"
    category = "files"
    summary = "Homework, milestone and health files: Google Drive, iCloud Drive (via the Mac) or the Mac itself."
    manage = "#settings"

    def status(self, ctx: Ctx) -> Status:
        path = settings.files_dir
        google = ctx.db.query(Integration).filter_by(family_id=ctx.family.id, provider="google").first()
        if google and (ctx.family.settings or {}).get("file_storage", "google") == "google":
            return Status("connected", "Google Drive")
        if "CloudDocs" in path:
            return Status("connected", "iCloud Drive (Files app on iPhones)")
        if "CloudStorage/GoogleDrive" in path:
            return Status("connected", "Google Drive for desktop")
        return Status("connected", "On the Mac")


# ---------------------------------------------------------------- doors & home

class AppleHome(Connector):
    key = "apple_home"
    name = "Apple Home (Matter locks, e.g. Philips 4000 series)"
    category = "home"
    availability = "mac"
    summary = "Lock and unlock through the Home app via Shortcuts on the Mac; Home automations report instantly."
    manage = "#home"

    def status(self, ctx: Ctx) -> Status:
        n = ctx.db.query(Lock).filter_by(family_id=ctx.family.id, provider="apple_home").count()
        return Status("connected", f"{n} lock(s)") if n else Status("off", "Add on the Doors tab")


class HomeAssistant(Connector):
    key = "homeassistant"
    name = "Home Assistant"
    category = "home"
    summary = "Any lock, sensor or device Home Assistant can reach, including Philips Wi-Fi locks via its community integration."
    fields = [Field("url", "Home Assistant URL", "url", placeholder="http://homeassistant.local:8123"),
              Field("token", "Long-lived access token", "password", secret=True,
                    help="Home Assistant → your profile → Security → Long-lived access tokens.")]
    docs_url = "https://www.home-assistant.io/installation/"

    def connect(self, ctx: Ctx, values: dict) -> str:
        url = values["url"].rstrip("/")
        with http.client() as c:
            r = c.get(f"{url}/api/", headers={"Authorization": f"Bearer {values['token']}"})
            r.raise_for_status()
        ctx.save(account=url, secret={"url": url, "token": values["token"]})
        return "Connected"


class PhilipsLock(Connector):
    key = "philips_lock"
    name = "Philips smart deadbolt"
    category = "home"
    availability = "bridge"
    summary = ("Philips Home Access has no public API. Matter models (4000 series) connect through SmartThings on a "
               "Galaxy phone or Apple Home on an iPhone; Wi-Fi models report through the lock webhook or Home Assistant.")
    manage = "#home"

    def status(self, ctx: Ctx) -> Status:
        locks = ctx.db.query(Lock).filter_by(family_id=ctx.family.id).all()
        real = [lk for lk in locks if lk.provider != "simulated"]
        if not real:
            return Status("off", "Add your lock on the Doors tab")
        heard = [lk for lk in real if lk.state != "unknown"]
        return Status("connected" if heard else "attention",
                      f"{len(real)} lock(s), via {', '.join(sorted({lk.provider for lk in real}))}"
                      if heard else "Added, but no status received yet")


class SmartThings(Connector):
    key = "smartthings"
    name = "Samsung SmartThings (Galaxy phones, Matter locks)"
    category = "home"
    summary = ("Lock status, door open/closed, battery, and lock/unlock for any lock in your SmartThings app, "
               "including a Philips Matter lock paired from your Galaxy phone. Official Samsung API, free.")
    fields = [Field("client_id", "OAuth Client ID"),
              Field("client_secret", "OAuth Client Secret", "password", secret=True)]
    steps = [
        "Pair the lock in the SmartThings app on your Galaxy phone (+ → Device → scan the lock's Matter code).",
        "On your laptop, install Node.js (nodejs.org), then run in Terminal:  npx @smartthings/cli apps:create",
        "Choose “OAuth-In App”, name it Family Hub, pick scopes r:devices:*, x:devices:* and r:locations:*, "
        "and enter the redirect URI shown below. Copy the Client ID and Client Secret it prints.",
        "Paste them here and press Connect, then sign in to Samsung and allow access. Your locks appear on the Doors tab.",
    ]
    docs_url = "https://developer.smartthings.com/docs/getting-started/authorization-and-permissions"

    def connect(self, ctx: Ctx, values: dict) -> str:
        from ..services import smartthings
        from .base import NeedsInput

        ctx.save(account="Signing in…", secret={"client_id": values["client_id"].strip(),
                                                "client_secret": values["client_secret"].strip()})
        raise NeedsInput("redirect", "Opening Samsung sign-in…", [], url=smartthings.start(ctx.db, ctx.family))

    def poll(self, ctx: Ctx) -> str:
        from ..services import locks, smartthings

        found = smartthings.discover_locks(ctx.db, ctx.family)
        locks.poll(ctx.db, ctx.family)
        integ = ctx.integration
        integ.account = f"{len(found)} lock(s)"
        ctx.db.commit()
        return f"{len(found)} lock(s) found"

    def status(self, ctx: Ctx) -> Status:
        integ = ctx.integration
        if integ is None:
            return Status("off", f"Redirect URI for the CLI: {smartthings_redirect()}")
        from ..crypto import decrypt_json

        if not decrypt_json(integ.secret_enc).get("refresh_token"):
            return Status("attention", "Finish signing in to Samsung (press Connect again)")
        return super().status(ctx)


def smartthings_redirect() -> str:
    from ..services import smartthings

    return smartthings.redirect_uri()


CATALOG: list[Connector] = [
    GoogleCalendar(), ICloudCalendar(), ICSFeeds(), HubFeed(),
    FindMy(), GoogleLocationSharing(), WifiPresence(), Shortcuts(),
    WebPush(), IMessage(), GoogleMessages(), GoogleChat(),
    GmailAppPassword(),
    FileStorage(),
    PhilipsLock(), SmartThings(), AppleHome(), HomeAssistant(),
]
BY_KEY = {c.key: c for c in CATALOG}
