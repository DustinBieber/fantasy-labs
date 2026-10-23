import datetime as dt
from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import utcnow


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    # Secret embedded in inbound webhook URLs (smart-lock events, automations).
    hook_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Short code a kid's device enters once to see the family's PIN sign-in screen.
    join_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    # Alert rule tuning, e.g. {"unlocked_minutes": 10, "quiet_start": "22:00", "quiet_end": "06:00"}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    members: Mapped[list["Member"]] = relationship(back_populates="family", cascade="all, delete-orphan")


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(10))  # "parent" | "child"
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pin_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    color: Mapped[str] = mapped_column(String(9), default="#4F7CFF")
    birthday: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    weekly_allowance_cents: Mapped[int] = mapped_column(Integer, default=0)
    share_location: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    family: Mapped[Family] = relationship(back_populates="members")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


# ---------------------------------------------------------------- chores & rewards

class Chore(Base):
    """A chore definition; ChoreInstance rows are the dated occurrences kids actually check off."""
    __tablename__ = "chores"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[int] = mapped_column(Integer, default=10)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    # "once" | "daily" | "weekly" | "monthly"
    recurrence: Mapped[str] = mapped_column(String(10), default="once")
    # For weekly chores: weekday numbers, Monday=0.
    weekdays: Mapped[list] = mapped_column(JSON, default=list)
    start_date: Mapped[dt.date] = mapped_column(Date)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChoreInstance(Base):
    __tablename__ = "chore_instances"
    __table_args__ = (UniqueConstraint("chore_id", "due_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chore_id: Mapped[int] = mapped_column(ForeignKey("chores.id", ondelete="CASCADE"), index=True)
    due_date: Mapped[dt.date] = mapped_column(Date, index=True)
    # "todo" | "submitted" | "approved" | "rejected"
    status: Mapped[str] = mapped_column(String(10), default="todo")
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)

    chore: Mapped[Chore] = relationship()


class PointsEntry(Base):
    """Append-only points ledger; a member's balance is the sum of their entries."""
    __tablename__ = "points_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Reward(Base):
    __tablename__ = "rewards"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    cost: Mapped[int] = mapped_column(Integer)
    icon: Mapped[str] = mapped_column(String(16), default="🎁")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Redemption(Base):
    __tablename__ = "redemptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    reward_id: Mapped[int] = mapped_column(ForeignKey("rewards.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    cost: Mapped[int] = mapped_column(Integer)
    # "pending" | "fulfilled" | "declined" — points are held when requested and refunded if declined.
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    reward: Mapped[Reward] = relationship()


# ---------------------------------------------------------------- calendar, homework, milestones

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("family_id", "source", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    start: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    member_ids: Mapped[list] = mapped_column(JSON, default=list)
    # "local" | "google" | "icloud"
    source: Mapped[str] = mapped_column(String(10), default="local")
    external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Push local events out to these providers on the next sync.
    push_to: Mapped[list] = mapped_column(JSON, default=list)
    pushed: Mapped[dict] = mapped_column(JSON, default=dict)  # provider -> remote id


class Homework(Base):
    __tablename__ = "homework"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    subject: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="todo")  # todo | in_progress | done
    notes: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[int] = mapped_column(Integer, default=0)  # optional bonus points when done
    points_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list] = mapped_column(JSON, default=list)  # [{name, url, drive_id}]


class Milestone(Base):
    __tablename__ = "milestones"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(40), default="general")
    notes: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- finances & health

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    # Set for a child's wallet (allowance, spending); null for household money.
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)  # positive = money in, negative = money out
    category: Mapped[str] = mapped_column(String(60), default="general")
    description: Mapped[str] = mapped_column(String(200), default="")
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    # Prevents the scheduler double-paying recurring items such as allowance.
    dedupe_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("family_id", "category"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    category: Mapped[str] = mapped_column(String(60))
    monthly_limit_cents: Mapped[int] = mapped_column(Integer)


class HealthRecord(Base):
    __tablename__ = "health_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    # appointment | medication | vital | allergy | immunization | note
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    value: Mapped[str] = mapped_column(String(60), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    next_due: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    attachments: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- location & home

class LocationPing(Base):
    __tablename__ = "location_pings"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Geofence(Base):
    __tablename__ = "geofences"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=150)
    notify_enter: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_leave: Mapped[bool] = mapped_column(Boolean, default=True)


class GeofenceState(Base):
    __tablename__ = "geofence_states"
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    geofence_id: Mapped[int] = mapped_column(ForeignKey("geofences.id", ondelete="CASCADE"), primary_key=True)
    inside: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Lock(Base):
    __tablename__ = "locks"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # "homeassistant" (control + state), "webhook" (state pushed in by an automation), "simulated" (demo)
    provider: Mapped[str] = mapped_column(String(16), default="webhook")
    # Provider-side identifier, e.g. Home Assistant entity id "lock.front_door".
    external_id: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(12), default="unknown")  # locked | unlocked | jammed | unknown
    door: Mapped[str] = mapped_column(String(8), default="unknown")  # open | closed | unknown
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_changed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    door_changed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    # Set once a "left unlocked/open" alert fires so it is not repeated until the state changes.
    stale_alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class LockEvent(Base):
    __tablename__ = "lock_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    lock_id: Mapped[int] = mapped_column(ForeignKey("locks.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(16))  # locked | unlocked | jammed | door_open | door_closed | battery_low
    actor: Mapped[str] = mapped_column(String(80), default="")
    source: Mapped[str] = mapped_column(String(16), default="")  # app | webhook | homeassistant | keypad ...
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ---------------------------------------------------------------- alerts & integrations

class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # door | location | chore | reward | homework | health | finance | system
    severity: Mapped[str] = mapped_column(String(8), default="info")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    # Only these roles see the alert in-app ("parent", "child"); empty = everyone.
    audience: Mapped[list] = mapped_column(JSON, default=list)
    read_by: Mapped[list] = mapped_column(JSON, default=list)
    delivery: Mapped[dict] = mapped_column(JSON, default=dict)  # channel id -> "sent" | error text
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    # ntfy | twilio_sms | gmail | google_chat | webhook
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(80))
    config_enc: Mapped[str] = mapped_column(Text)  # encrypted JSON
    min_severity: Mapped[str] = mapped_column(String(8), default="warning")
    kinds: Mapped[list] = mapped_column(JSON, default=list)  # alert kinds to forward; empty = all
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Integration(Base):
    """Credentials for an external account: provider is "google", "icloud" or "homeassistant"."""
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("family_id", "provider"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    account: Mapped[str] = mapped_column(String(200), default="")
    secret_enc: Mapped[str] = mapped_column(Text)  # encrypted JSON (tokens / passwords)
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret settings, e.g. calendar id
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
