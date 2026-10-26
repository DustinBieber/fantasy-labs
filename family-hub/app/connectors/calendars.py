"""Calendar connectors.

  google      Google Calendar two-way sync (OAuth, set up on this page)
  icloud      iCloud Calendar two-way sync (CalDAV + app-specific password)
  ics_feeds   Any calendar link: Google's "secret address in iCal format" (no Google Cloud setup needed),
              school / sports / team calendars, Outlook, TeamSnap... read-only, refreshed every 30 min
  hub_feed    The hub's own calendar as a link everyone can subscribe to on iPhone or Google Calendar
"""
import hashlib
from datetime import datetime, timedelta

from ..config import settings
from ..models import Event, Member
from ..services import http, icloud
from ..timeutil import to_utc_naive, utcnow
from .base import Connector, Ctx, Field, Status


class GoogleCalendar(Connector):
    key = "google"
    name = "Google (Calendar, Drive, Gmail)"
    category = "calendar"
    summary = "Two-way Google Calendar sync, Drive for attachments, Gmail for alert emails."
    manage = "#connections/google"
    docs_url = "https://console.cloud.google.com/apis/credentials"

    def status(self, ctx: Ctx) -> Status:
        if not (settings.google_client_id and settings.google_client_secret):
            return Status("off", "Needs a free Google Cloud OAuth client first (steps below). Or use the Google "
                                 "secret iCal link in “Calendar links” and the Gmail app password for email today.")
        return super().status(ctx)


class ICloudCalendar(Connector):
    key = "icloud"
    name = "iCloud Calendar (iPhone)"
    category = "calendar"
    summary = "Two-way sync with the calendars on your iPhones."
    fields = [
        Field("username", "Apple ID", "email"),
        Field("password", "App-specific password", "password", secret=True, placeholder="xxxx-xxxx-xxxx-xxxx",
              help="Make one at appleid.apple.com → Sign-In and Security → App-Specific Passwords."),
        Field("calendar_name", "Calendar name (blank = first calendar)", required=False, placeholder="Family"),
    ]
    poll_minutes = 15
    docs_url = "https://support.apple.com/en-us/102654"

    def connect(self, ctx: Ctx, values: dict) -> str:
        secret = {"username": values["username"], "password": values["password"], "url": ""}
        options = {"calendar_name": values.get("calendar_name", "")}
        name = icloud.verify(secret, options)
        integ = ctx.save(account=values["username"], secret=secret, options={**options, "resolved_calendar": name})
        r = icloud.sync_calendar(ctx.db, integ)
        return f"Connected to “{name}”: {r['pulled']} events"

    def poll(self, ctx: Ctx) -> str:
        r = icloud.sync_calendar(ctx.db, ctx.integration)
        return f"{r['pulled']} in, {r['pushed']} out"


def parse_feed_lines(text: str, members: list[Member]) -> list[dict]:
    """Each line: URL, or "Name | URL", or "Name | URL | Sam, Alex" to tag events with people."""
    feeds = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        if len(parts) == 1:
            name, url, who = "", parts[0], ""
        else:
            name, url, who = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Not a calendar link: {url[:60]}")
        names = {n.strip().lower() for n in who.split(",") if n.strip()}
        feeds.append({"id": hashlib.sha1(url.encode()).hexdigest()[:10], "name": name or url.split("/")[2],
                      "url": url, "member_ids": [m.id for m in members if m.name.lower() in names]})
    return feeds


def _as_utc(value) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        return to_utc_naive(value), False
    return datetime.combine(value, datetime.min.time()), True


class ICSFeeds(Connector):
    key = "ics_feeds"
    name = "Calendar links (Google secret link, school, sports…)"
    category = "calendar"
    summary = ("Subscribe to any calendar link, read-only. The fastest way to get Google Calendar in today "
               "with no Google Cloud setup, plus school and team schedules.")
    fields = [Field("feeds", "Calendar links, one per line", "textarea", secret=True,
                    placeholder="Mom's Google | https://calendar.google.com/calendar/ical/…/basic.ics\n"
                                "Lincoln Elementary | webcal://school.example/cal.ics | Sam",
                    help="Format: Name | link | people (optional). Google Calendar: Settings → your calendar → "
                         "Integrate calendar → Secret address in iCal format.")]
    poll_minutes = 30

    def connect(self, ctx: Ctx, values: dict) -> str:
        members = ctx.db.query(Member).filter_by(family_id=ctx.family.id).all()
        feeds = parse_feed_lines(values["feeds"], members)
        if not feeds:
            raise ValueError("Add at least one calendar link")
        ctx.save(account=f"{len(feeds)} calendar(s)", secret={"feeds": values["feeds"]},
                 options={"feeds": [{k: f[k] for k in ("id", "name", "member_ids")} for f in feeds]})
        return self.poll(ctx)

    def poll(self, ctx: Ctx) -> str:
        import icalendar
        import recurring_ical_events

        members = ctx.db.query(Member).filter_by(family_id=ctx.family.id).all()
        feeds = parse_feed_lines(ctx.secret().get("feeds", ""), members)
        now = utcnow()
        window_start, window_end = now - timedelta(days=7), now + timedelta(days=90)
        total, errors = 0, []
        with http.client(timeout=30) as c:
            for feed in feeds:
                try:
                    resp = c.get(feed["url"], headers={"User-Agent": "FamilyHub/1.0"})
                    resp.raise_for_status()
                    cal = icalendar.Calendar.from_ical(resp.content)
                    occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
                except Exception as exc:
                    errors.append(f"{feed['name']}: {str(exc)[:120]}")
                    continue
                seen = set()
                for comp in occurrences:
                    if comp.get("dtstart") is None:
                        continue
                    start, all_day = _as_utc(comp.get("dtstart").dt)
                    end = _as_utc(comp.get("dtend").dt)[0] if comp.get("dtend") else None
                    ext = f"{feed['id']}:{comp.get('uid', '')}:{start.isoformat()}"[:300]
                    seen.add(ext)
                    ev = ctx.db.query(Event).filter_by(family_id=ctx.family.id, source="ics", external_id=ext).first()
                    if ev is None:
                        ev = Event(family_id=ctx.family.id, source="ics", external_id=ext, member_ids=feed["member_ids"])
                        ctx.db.add(ev)
                    ev.title = str(comp.get("summary") or "(no title)")[:200]
                    ev.start, ev.end, ev.all_day = start, end, all_day
                    ev.location = str(comp.get("location") or "")[:200]
                    ev.notes = (f"From {feed['name']}\n" + str(comp.get("description") or ""))[:4000]
                    total += 1
                for ev in ctx.db.query(Event).filter(Event.family_id == ctx.family.id, Event.source == "ics",
                                                     Event.external_id.like(f"{feed['id']}:%"),
                                                     Event.start >= window_start, Event.start <= window_end):
                    if ev.external_id not in seen:
                        ctx.db.delete(ev)
        ctx.db.commit()
        if errors and not total:
            raise RuntimeError("; ".join(errors))
        return f"{total} events" + (f" (problems: {'; '.join(errors)})" if errors else "")

    def disconnect(self, ctx: Ctx) -> None:
        ctx.db.query(Event).filter_by(family_id=ctx.family.id, source="ics").delete()
        super().disconnect(ctx)


class HubFeed(Connector):
    key = "hub_feed"
    name = "Family Hub calendar on every phone"
    category = "calendar"
    summary = ("Subscribe to the hub's calendar (events, homework due dates, appointments, birthdays) from "
               "iPhone Calendar or Google Calendar. No setup on the hub.")
    manage = "#connections/hub_feed"

    def status(self, ctx: Ctx) -> Status:
        return Status("connected", "Ready to subscribe")
