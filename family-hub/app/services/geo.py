"""Location pings and geofence enter/leave alerts.

Sources, all free and without extra apps:
  - the hub's home-screen web app while it's open (precise pings),
  - iPhone Shortcuts automations ("When I arrive at / leave Home") hitting the presence webhook,
    or posting "Get Current Location" to the location webhook,
  - Wi-Fi presence: the Mac running the hub sees family phones on the home network (services/presence.py).
OwnTracks (optional app) also works for continuous background tracking.
"""
import math

from sqlalchemy.orm import Session

from ..models import Family, Geofence, GeofenceState, LocationPing, Member
from ..timeutil import utcnow
from .notify import raise_alert

EARTH_RADIUS_M = 6_371_000


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def record_ping(db: Session, family: Family, member: Member, lat: float, lng: float,
                accuracy_m: float | None = None, battery: float | None = None) -> list[str]:
    """Store a ping and return the geofence transitions it caused, e.g. ["arrived at School"]."""
    db.add(LocationPing(member_id=member.id, lat=lat, lng=lng, accuracy_m=accuracy_m, battery=battery))
    transitions = []
    # A wildly inaccurate fix (cell-tower level) would flap geofences; store it but don't evaluate.
    if accuracy_m is not None and accuracy_m > 1000:
        db.commit()
        return transitions
    slack = max(25.0, accuracy_m or 0)
    for fence in db.query(Geofence).filter_by(family_id=family.id):
        d = distance_m(lat, lng, fence.lat, fence.lng)
        state = db.get(GeofenceState, (member.id, fence.id))
        # Hysteresis: must be inside the radius to enter, and clearly outside (radius + slack) to leave.
        if d <= fence.radius_m:
            inside = True
        elif d > fence.radius_m + slack:
            inside = False
        else:
            continue
        if state is None:
            db.add(GeofenceState(member_id=member.id, geofence_id=fence.id, inside=inside))
            db.flush()
            continue  # first observation sets a baseline without alerting
        if set_inside(db, family, member, fence, inside, state):
            transitions.append(f"{'arrived at' if inside else 'left'} {fence.name}")
    db.commit()
    return transitions


def set_inside(db: Session, family: Family, member: Member, fence: Geofence, inside: bool,
               state: GeofenceState | None = None, source: str = "") -> bool:
    """Record that a member is inside/outside a place; alerts on a change. Returns True if it changed."""
    state = state or db.get(GeofenceState, (member.id, fence.id))
    if state is None:
        state = GeofenceState(member_id=member.id, geofence_id=fence.id, inside=not inside)
        db.add(state)
    if state.inside == inside:
        return False
    state.inside, state.changed_at = inside, utcnow()
    db.flush()
    verb = "arrived at" if inside else "left"
    if (inside and fence.notify_enter) or (not inside and fence.notify_leave):
        raise_alert(db, family, "location", f"{member.name} {verb} {fence.name}",
                    f"Detected by {source}." if source else "", severity="info", member_id=member.id,
                    audience=["parent"])
    db.commit()
    return True


def find_place(db: Session, family: Family, name: str) -> Geofence | None:
    for fence in db.query(Geofence).filter_by(family_id=family.id):
        if fence.name.strip().lower() == name.strip().lower():
            return fence
    return None
