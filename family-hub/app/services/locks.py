"""Smart-lock state, control and door alert rules.

Philips smart deadbolts (Philips Home Access) do not publish a public developer API, so the hub
reaches them through one of these providers:

  apple_home    - The lock is in the Apple Home app (Matter models like the Philips 4000 series pair
                  straight into Apple Home; needs a HomePod/Apple TV as home hub). The Mac runs Shortcuts
                  named "Lock <name>", "Unlock <name>" and optionally "Status <name>". For instant
                  status, an Apple Home automation ("When <lock> unlocks") opens the lock webhook URL.
  smartthings   - Samsung SmartThings (the natural home for Matter locks on Galaxy phones): official API,
                  status polled every minute, lock/unlock supported.
  homeassistant - Home Assistant exposes the lock as `lock.<name>`. Matter models (e.g. the Philips
                  4000 series, Matter over Thread) pair with HA's Matter integration; Wi-Fi models need a
                  community integration. The hub polls its state and can lock/unlock it.
  webhook       - Any automation that sees the lock (Home Assistant, SmartThings, IFTTT, an Alexa or
                  Google Home routine via a bridge) POSTs events to the family's webhook URL.
                  State and alerts only; lock/unlock stays in the Philips app.
  simulated     - A fake lock for trying the app out.
"""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..crypto import decrypt_json
from ..models import Family, Geofence, GeofenceState, Integration, Lock, LockEvent, Member
from ..timeutil import local_now, utcnow
from . import http, macos
from .notify import raise_alert

STATE_EVENTS = {"locked": "locked", "unlocked": "unlocked", "jammed": "jammed"}
DOOR_EVENTS = {"door_open": "open", "door_closed": "closed"}
VALID_EVENTS = set(STATE_EVENTS) | set(DOOR_EVENTS) | {"battery_low"}
DEFAULTS = {"unlocked_minutes": 10, "door_open_minutes": 5, "quiet_start": "22:00", "quiet_end": "06:00",
            "notify_all_unlocks": False, "battery_low_percent": 20}


def family_setting(family: Family, key: str):
    return (family.settings or {}).get(key, DEFAULTS.get(key))


def in_quiet_hours(family: Family, now: datetime | None = None) -> bool:
    local = local_now(family.timezone, now).strftime("%H:%M")
    start, end = family_setting(family, "quiet_start"), family_setting(family, "quiet_end")
    return (start <= local < end) if start <= end else (local >= start or local < end)


def record_event(db: Session, family: Family, lock: Lock, event: str, *, actor: str = "",
                 source: str = "", battery: int | None = None, now: datetime | None = None) -> LockEvent | None:
    """Apply an observed lock event, log it, and raise any alerts. Repeated identical states are ignored."""
    if event not in VALID_EVENTS:
        raise HTTPException(422, f"Unknown lock event '{event}'")
    now = now or utcnow()
    battery_alert = event == "battery_low"
    if battery is not None:
        low = family_setting(family, "battery_low_percent")
        battery_alert = battery_alert or (battery <= low and (lock.battery is None or lock.battery > low))
        lock.battery = battery

    changed = battery_alert
    if event in STATE_EVENTS and lock.state != STATE_EVENTS[event]:
        lock.state, lock.state_changed_at, lock.stale_alerted, changed = STATE_EVENTS[event], now, False, True
    elif event in DOOR_EVENTS and lock.door != DOOR_EVENTS[event]:
        lock.door, lock.door_changed_at, lock.stale_alerted, changed = DOOR_EVENTS[event], now, False, True

    if not changed:
        db.commit()
        return None
    row = LockEvent(lock_id=lock.id, event=event, actor=actor[:80], source=source[:16], created_at=now)
    db.add(row)
    db.commit()

    who = f" by {actor}" if actor else ""
    parents = ["parent"]
    if event == "jammed":
        raise_alert(db, family, "door", f"{lock.name} is jammed", "The bolt did not fully extend. Check the door.",
                    severity="critical", audience=parents)
    elif event == "unlocked" and in_quiet_hours(family, now):
        raise_alert(db, family, "door", f"{lock.name} unlocked during quiet hours{who}",
                    f"Unlocked at {local_now(family.timezone, now):%-I:%M %p}.", severity="warning", audience=parents)
    elif event == "door_open" and in_quiet_hours(family, now):
        raise_alert(db, family, "door", f"{lock.name} opened during quiet hours",
                    f"Opened at {local_now(family.timezone, now):%-I:%M %p}.", severity="warning", audience=parents)
    elif event == "unlocked" and family_setting(family, "notify_all_unlocks"):
        raise_alert(db, family, "door", f"{lock.name} unlocked{who}", severity="info", audience=parents)
    if battery_alert:
        raise_alert(db, family, "door", f"{lock.name} battery is low",
                    f"Battery at {lock.battery}%." if lock.battery is not None else "Replace the batteries soon.",
                    severity="warning", audience=parents)
    return row


def check_stale(db: Session, family: Family, now: datetime | None = None) -> None:
    """Alert once when a door is left unlocked/open too long, or unlocked while nobody is home."""
    now = now or utcnow()
    nobody_home = _nobody_home(db, family)
    for lock in db.query(Lock).filter_by(family_id=family.id):
        if lock.stale_alerted:
            continue
        unlocked_for = now - lock.state_changed_at
        open_for = now - lock.door_changed_at
        if lock.state == "unlocked" and nobody_home:
            raise_alert(db, family, "door", f"Nobody is home and {lock.name} is unlocked",
                        "Everyone sharing location has left Home.", severity="critical", audience=["parent"])
        elif lock.state == "unlocked" and unlocked_for >= timedelta(minutes=family_setting(family, "unlocked_minutes")):
            raise_alert(db, family, "door", f"{lock.name} has been unlocked for {int(unlocked_for.total_seconds() // 60)} min",
                        severity="warning", audience=["parent"])
        elif lock.door == "open" and open_for >= timedelta(minutes=family_setting(family, "door_open_minutes")):
            raise_alert(db, family, "door", f"{lock.name} door has been open for {int(open_for.total_seconds() // 60)} min",
                        severity="warning", audience=["parent"])
        else:
            continue
        lock.stale_alerted = True
        db.commit()


def _nobody_home(db: Session, family: Family) -> bool:
    home = db.query(Geofence).filter(Geofence.family_id == family.id).all()
    home = next((g for g in home if g.name.strip().lower() == "home"), None)
    if home is None:
        return False
    sharers = db.query(Member).filter_by(family_id=family.id, share_location=True).all()
    if not sharers:
        return False
    states = {s.member_id: s.inside for s in db.query(GeofenceState).filter_by(geofence_id=home.id)}
    # Unknown state (never reported) counts as "maybe home" to avoid false alarms.
    return all(states.get(m.id) is False for m in sharers)


# ---------------------------------------------------------------- providers

def _ha(db: Session, family_id: int) -> dict:
    integ = db.query(Integration).filter_by(family_id=family_id, provider="homeassistant").first()
    if not integ:
        raise HTTPException(400, "Connect Home Assistant in Settings first")
    secret = decrypt_json(integ.secret_enc)
    return {"url": secret["url"].rstrip("/"), "headers": {"Authorization": f"Bearer {secret['token']}"}}


def command(db: Session, family: Family, lock: Lock, action: str, actor: Member) -> None:
    if action not in ("lock", "unlock"):
        raise HTTPException(422, "action must be lock or unlock")
    if lock.provider == "webhook":
        raise HTTPException(400, "This lock reports status only. Use the Philips app (or connect it through "
                                 "Home Assistant) to lock or unlock it remotely.")
    if lock.provider == "apple_home":
        out = macos.run_shortcut(f"{'Lock' if action == 'lock' else 'Unlock'} {lock.external_id}")
        state = parse_state(out)
        if state == "jammed":
            record_event(db, family, lock, "jammed", actor=actor.name, source="apple_home")
            return
    if lock.provider == "smartthings":
        from . import smartthings

        smartthings.command(db, family, lock, action)
    if lock.provider == "homeassistant":
        ha = _ha(db, family.id)
        with http.client() as c:
            c.post(f"{ha['url']}/api/services/lock/{action}", headers=ha["headers"],
                   json={"entity_id": lock.external_id}).raise_for_status()
    record_event(db, family, lock, "locked" if action == "lock" else "unlocked", actor=actor.name, source="app")


def parse_state(text: str) -> str | None:
    """Read a lock state out of a Shortcut's output ("Locked", "Unsecured", HomeKit codes 0/1/2...)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if "jam" in t or t == "2":
        return "jammed"
    if "unlock" in t or "unsecur" in t or t in ("0", "false", "no"):
        return "unlocked"
    if "lock" in t or "secur" in t or t in ("1", "true", "yes"):
        return "locked"
    return None


def poll(db: Session, family: Family) -> None:
    """Refresh locks the hub can query (the webhook covers instant updates; this catches misses)."""
    apple = db.query(Lock).filter_by(family_id=family.id, provider="apple_home").all()
    if apple:
        available = set(macos.list_shortcuts())
        for lock in apple:
            name = f"Status {lock.external_id}"
            if name in available:
                state = parse_state(macos.run_shortcut(name))
                if state:
                    record_event(db, family, lock, state, source="apple_home")
    for lock in db.query(Lock).filter_by(family_id=family.id, provider="smartthings").all():
        from . import smartthings

        st = smartthings.read_state(db, family, lock)
        if st["state"]:
            record_event(db, family, lock, st["state"], source="smartthings", battery=st["battery"])
        elif st["battery"] is not None:
            lock.battery = st["battery"]
            db.commit()
        if st["door"]:
            record_event(db, family, lock, f"door_{st['door']}", source="smartthings")
    locks = db.query(Lock).filter_by(family_id=family.id, provider="homeassistant").all()
    if not locks:
        return
    ha = _ha(db, family.id)
    with http.client() as c:
        for lock in locks:
            resp = c.get(f"{ha['url']}/api/states/{lock.external_id}", headers=ha["headers"])
            if resp.status_code != 200:
                continue
            data = resp.json()
            state = data.get("state")
            battery = data.get("attributes", {}).get("battery_level")
            if state == "open":  # HA reports "open" for locks that also sense the door
                record_event(db, family, lock, "door_open", source="homeassistant", battery=battery)
            elif state in STATE_EVENTS:
                record_event(db, family, lock, state, source="homeassistant", battery=battery)
