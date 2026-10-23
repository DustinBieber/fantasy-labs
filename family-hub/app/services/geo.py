"""Location pings and geofence enter/leave alerts.

Pings come from the hub's own installable web app (foreground location), or from any tracker that can
POST to the family webhook: e.g. the OwnTracks app (iPhone/Android, background), Home Assistant's
companion app, or an iOS Shortcuts automation ("When I arrive/leave ...").
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
            continue  # first observation sets a baseline without alerting
        if state.inside == inside:
            continue
        state.inside, state.changed_at = inside, utcnow()
        verb = "arrived at" if inside else "left"
        transitions.append(f"{verb} {fence.name}")
        if (inside and fence.notify_enter) or (not inside and fence.notify_leave):
            raise_alert(db, family, "location", f"{member.name} {verb} {fence.name}",
                        severity="info", member_id=member.id, audience=["parent"])
    db.commit()
    return transitions
