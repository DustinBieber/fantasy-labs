from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alert, Member, PushSubscription
from ..security import current_member
from ..services import push

router = APIRouter(prefix="/api/push", tags=["push notifications"])


@router.get("/key")
def public_key(db: Session = Depends(get_db)):
    return {"public_key": push.vapid_keys(db)["public"]}


class SubKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class SubscribeIn(BaseModel):
    endpoint: str = Field(pattern=r"^https://", max_length=1000)
    keys: SubKeys
    device: str = Field(default="", max_length=120)


@router.post("/subscribe")
def subscribe(body: SubscribeIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    sub = db.query(PushSubscription).filter_by(endpoint=body.endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint)
        db.add(sub)
    # A shared tablet re-subscribed by someone else now belongs to them.
    sub.member_id, sub.p256dh, sub.auth, sub.device = me.id, body.keys.p256dh, body.keys.auth, body.device
    db.commit()
    return {"ok": True, "devices": db.query(PushSubscription).filter_by(member_id=me.id).count()}


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
def unsubscribe(body: UnsubscribeIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter_by(endpoint=body.endpoint, member_id=me.id).delete()
    db.commit()
    return {"ok": True}


@router.post("/test")
def test(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    probe = Alert(id=0, family_id=me.family_id, kind="system", severity="info", title="Notifications are on ✅",
                  body=f"{me.name}, this device will get Family Hub alerts.", member_id=me.id, audience=[])
    return {"sent": push.push_alert_to(db, me, probe)}
