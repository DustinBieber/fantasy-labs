"""Location sharing, geofences, smart locks, and the unauthenticated-but-secret inbound webhooks."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Family, Geofence, GeofenceState, LocationPing, Lock, LockEvent, Member
from ..security import current_member, ensure_self_or_parent, get_owned, require_parent
from ..services import geo, locks
from ..services.notify import SEVERITY_RANK, raise_alert
from ..timeutil import iso, utcnow

router = APIRouter(prefix="/api", tags=["location & home"])


# ---------------------------------------------------------------- location

class PingIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    battery: float | None = Field(default=None, ge=0, le=100)


@router.post("/location")
def post_location(body: PingIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if not me.share_location:
        raise HTTPException(409, "Location sharing is turned off for you")
    return {"transitions": geo.record_ping(db, me.family, me, body.lat, body.lng, body.accuracy_m, body.battery)}


def _ping_out(p: LocationPing) -> dict:
    return {"lat": p.lat, "lng": p.lng, "accuracy_m": p.accuracy_m, "battery": p.battery, "at": iso(p.created_at)}


@router.get("/location/latest")
def latest(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    members = db.query(Member).filter_by(family_id=me.family_id).all()
    if me.role != "parent":
        members = [me]
    fences = {g.id: g.name for g in db.query(Geofence).filter_by(family_id=me.family_id)}
    out = []
    for m in members:
        p = db.query(LocationPing).filter_by(member_id=m.id).order_by(LocationPing.created_at.desc()).first()
        places = [fences[s.geofence_id] for s in db.query(GeofenceState).filter_by(member_id=m.id, inside=True)
                  if s.geofence_id in fences]
        out.append({"member_id": m.id, "name": m.name, "color": m.color, "share_location": m.share_location,
                    "last": _ping_out(p) if p and m.share_location else None, "places": places})
    return out


@router.get("/location/history/{member_id}")
def history(member_id: int, hours: int = 24, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    ensure_self_or_parent(me, member_id)
    get_owned(db, Member, member_id, me.family_id)
    since = utcnow() - timedelta(hours=min(max(hours, 1), 24 * 7))
    rows = db.query(LocationPing).filter(LocationPing.member_id == member_id, LocationPing.created_at >= since)
    return [_ping_out(p) for p in rows.order_by(LocationPing.created_at)]


def fence_out(g: Geofence) -> dict:
    return {"id": g.id, "name": g.name, "lat": g.lat, "lng": g.lng, "radius_m": g.radius_m,
            "notify_enter": g.notify_enter, "notify_leave": g.notify_leave}


class FenceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=150, ge=25, le=50_000)
    notify_enter: bool = True
    notify_leave: bool = True


@router.get("/geofences")
def list_fences(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    return [fence_out(g) for g in db.query(Geofence).filter_by(family_id=me.family_id).order_by(Geofence.name)]


@router.post("/geofences")
def add_fence(body: FenceIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    g = Geofence(family_id=me.family_id, **body.model_dump())
    db.add(g)
    db.commit()
    return fence_out(g)


@router.put("/geofences/{fence_id}")
def update_fence(fence_id: int, body: FenceIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    g = get_owned(db, Geofence, fence_id, me.family_id)
    for k, v in body.model_dump().items():
        setattr(g, k, v)
    db.query(GeofenceState).filter_by(geofence_id=g.id).delete()  # re-baseline after moving a fence
    db.commit()
    return fence_out(g)


@router.delete("/geofences/{fence_id}")
def delete_fence(fence_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    g = get_owned(db, Geofence, fence_id, me.family_id)
    db.query(GeofenceState).filter_by(geofence_id=g.id).delete()
    db.delete(g)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- locks

def lock_out(lk: Lock) -> dict:
    return {"id": lk.id, "name": lk.name, "provider": lk.provider, "external_id": lk.external_id,
            "state": lk.state, "door": lk.door, "battery": lk.battery,
            "state_changed_at": iso(lk.state_changed_at), "door_changed_at": iso(lk.door_changed_at),
            "can_control": lk.provider in ("apple_home", "homeassistant", "smartthings", "simulated")}


class LockIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default="webhook", pattern="^(apple_home|homeassistant|smartthings|webhook|simulated)$")
    external_id: str = Field(min_length=1, max_length=120)


@router.get("/locks")
def list_locks(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return [lock_out(lk) for lk in db.query(Lock).filter_by(family_id=me.family_id).order_by(Lock.name)]


@router.post("/locks")
def add_lock(body: LockIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    if body.provider == "apple_home":
        from ..services import macos

        if not macos.is_mac():
            raise HTTPException(400, "Apple Home locks need Family Hub running on your Mac")
        needed = [f"Lock {body.external_id}", f"Unlock {body.external_id}"]
        missing = [n for n in needed if n not in set(macos.list_shortcuts())]
        if missing:
            raise HTTPException(422, "Create these shortcuts in the Shortcuts app on the Mac first: "
                                     + ", ".join(f"“{n}”" for n in missing))
    lk = Lock(family_id=me.family_id, **body.model_dump(), state="unknown", door="unknown")
    if lk.provider == "simulated":
        lk.state, lk.door = "locked", "closed"
    db.add(lk)
    db.commit()
    if lk.provider == "homeassistant":
        try:
            locks.poll(db, me.family)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"Lock saved, but Home Assistant didn't answer: {exc}") from exc
    return lock_out(lk)


@router.delete("/locks/{lock_id}")
def delete_lock(lock_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.delete(get_owned(db, Lock, lock_id, me.family_id))
    db.commit()
    return {"ok": True}


class CommandIn(BaseModel):
    action: str = Field(pattern="^(lock|unlock)$")


@router.post("/locks/{lock_id}/command")
def lock_command(lock_id: int, body: CommandIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    lk = get_owned(db, Lock, lock_id, me.family_id)
    try:
        locks.command(db, me.family, lk, body.action, me)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"The lock didn't respond: {exc}") from exc
    return lock_out(lk)


@router.get("/locks/{lock_id}/events")
def lock_events(lock_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    lk = get_owned(db, Lock, lock_id, me.family_id)
    rows = db.query(LockEvent).filter_by(lock_id=lk.id).order_by(LockEvent.created_at.desc()).limit(200)
    return [{"event": e.event, "actor": e.actor, "source": e.source, "at": iso(e.created_at)} for e in rows]


# ---------------------------------------------------------------- inbound webhooks

def _family_by_hook(db: Session, token: str) -> Family:
    fam = db.query(Family).filter_by(hook_token=token).first()
    if not fam:
        raise HTTPException(404, "Unknown webhook")
    return fam


class LockHookIn(BaseModel):
    lock: str = Field(description="The lock's external id or name, e.g. 'lock.front_door' or 'Front Door'")
    event: str = Field(description="locked | unlocked | jammed | door_open | door_closed | battery_low")
    actor: str = ""
    battery: int | None = Field(default=None, ge=0, le=100)


@router.api_route("/hooks/{token}/lock", methods=["GET", "POST"])
async def lock_hook(token: str, request: Request, db: Session = Depends(get_db)):
    """JSON body, or query string (?lock=Front%20Door&event=unlocked) so an Apple Home automation can just open a URL."""
    fam = _family_by_hook(db, token)
    data = dict(request.query_params)
    if request.method == "POST":
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            data.update(payload)
    try:
        body = LockHookIn(**data)
    except ValueError as exc:
        raise HTTPException(422, f"Bad lock event: {exc}") from exc
    lks = db.query(Lock).filter_by(family_id=fam.id).all()
    lk = next((x for x in lks if x.external_id == body.lock), None) or \
        next((x for x in lks if x.name.lower() == body.lock.lower()), None)
    if lk is None:
        raise HTTPException(404, f"No lock named '{body.lock}'. Add it on the Doors tab first.")
    event = body.event.strip().lower()
    # Home Assistant state names: "open" means the door sensor tripped; transitional states carry no news.
    event = {"open": "door_open", "opening": "door_open", "closed": "door_closed"}.get(event, event)
    if event in ("locking", "unlocking"):
        return lock_out(lk)
    locks.record_event(db, fam, lk, event, actor=body.actor, source="webhook", battery=body.battery)
    return lock_out(lk)


@router.post("/hooks/{token}/location")
async def location_hook(token: str, request: Request, member: int, db: Session = Depends(get_db)):
    """Accepts {"lat","lng","accuracy_m","battery"} or an OwnTracks HTTP payload ({"_type":"location","lat","lon","acc","batt"})."""
    fam = _family_by_hook(db, token)
    m = db.get(Member, member)
    if not m or m.family_id != fam.id:
        raise HTTPException(404, "Unknown member")
    data = await request.json()
    if data.get("_type", "location") != "location":
        return []  # OwnTracks also sends waypoints/transitions; acknowledge and ignore
    try:
        ping = PingIn(lat=data["lat"], lng=data.get("lng", data.get("lon")),
                      accuracy_m=data.get("accuracy_m", data.get("acc")), battery=data.get("battery", data.get("batt")))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"Bad location payload: {exc}") from exc
    if m.share_location:
        geo.record_ping(db, fam, m, ping.lat, ping.lng, ping.accuracy_m, ping.battery)
    return []  # OwnTracks expects a JSON array back


@router.api_route("/hooks/{token}/presence", methods=["GET", "POST"])
async def presence_hook(token: str, request: Request, member: int, place: str = "", event: str = "",
                        db: Session = Depends(get_db)):
    """For iPhone Shortcuts "Arrive"/"Leave" automations: ?member=3&place=Home&event=arrive (or leave).

    Values may also come in a JSON body. GET is accepted so a bare URL works anywhere.
    """
    fam = _family_by_hook(db, token)
    m = db.get(Member, member)
    if not m or m.family_id != fam.id:
        raise HTTPException(404, "Unknown member")
    if request.method == "POST":
        try:
            data = await request.json()
        except ValueError:
            data = {}
        if isinstance(data, dict):
            place, event = data.get("place", place), data.get("event", event)
    event = str(event).strip().lower()
    if event not in ("arrive", "arrived", "enter", "leave", "left", "exit"):
        raise HTTPException(422, "event must be arrive or leave")
    fence = geo.find_place(db, fam, str(place) or "Home")
    if fence is None:
        raise HTTPException(404, f"No place named '{place}'. Add it on the Map tab.")
    changed = m.share_location and geo.set_inside(db, fam, m, fence, event in ("arrive", "arrived", "enter"),
                                                  source="iPhone Shortcut")
    return {"ok": True, "changed": bool(changed)}


@router.get("/presence/devices")
def lan_devices(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    """Devices the hub's computer can see on the home network, to help match phones to people."""
    from ..services import presence

    try:
        devices = presence.scan(resolve_names=True)
    except Exception as exc:
        raise HTTPException(500, f"Network scan failed: {exc}") from exc
    owners = {mac: m.name for m in db.query(Member).filter_by(family_id=me.family_id) for mac in (m.wifi_macs or [])}
    return [{**d, "member": owners.get(d["mac"])} for d in devices]


class AlertHookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    severity: str = Field(default="info", pattern="^(info|warning|critical)$")
    kind: str = Field(default="system", max_length=24)


@router.post("/hooks/{token}/alert")
def alert_hook(token: str, body: AlertHookIn, db: Session = Depends(get_db)):
    """Lets any automation (garage left open, water leak, doorbell) raise a hub alert."""
    fam = _family_by_hook(db, token)
    if body.severity not in SEVERITY_RANK:
        raise HTTPException(422, "Bad severity")
    a = raise_alert(db, fam, body.kind, body.title, body.body, severity=body.severity, audience=["parent"])
    return {"id": a.id}
