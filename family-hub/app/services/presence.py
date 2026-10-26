"""Wi-Fi presence: the computer running the hub notices family phones on the home network.

No app is needed on the phones. Each person's phone Wi-Fi address (iPhone: Settings > Wi-Fi > (i) >
"Wi-Fi Address" with Private Wi-Fi Address set to "Fixed"; Android: Wi-Fi network details) is saved on
their profile. Every scheduler tick the hub nudges every address on the local /24 so the OS refreshes
its ARP table, then reads `arp -an`. A phone seen recently counts as at Home; one unseen for
`wifi_away_minutes` (default 15, since sleeping phones drop off briefly) counts as away.
"""
import ipaddress
import re
import socket
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Family, GeofenceState, Member
from ..timeutil import utcnow
from . import geo, macos

ARP_LINE = re.compile(r"^(?P<name>\S+) \((?P<ip>[\d.]+)\) at (?P<mac>[0-9a-fA-F:]+)")
_cache: dict = {"at": 0.0, "seen": set()}
# Shown on the Map tab so a missing macOS "Local Network" permission is easy to spot.
status: dict = {"last_scan": None, "devices": 0, "error": ""}


def normalize_mac(mac: str) -> str:
    """macOS prints "a:b:3:..." without leading zeros; store and compare as "0a:0b:03:..."."""
    parts = re.split(r"[:\-]", mac.strip().lower())
    if len(parts) != 6 or not all(re.fullmatch(r"[0-9a-f]{1,2}", p) for p in parts):
        raise ValueError(f"'{mac}' doesn't look like a Wi-Fi address (6 pairs like a1:b2:c3:d4:e5:f6)")
    return ":".join(p.zfill(2) for p in parts)


def local_ipv4() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]


def parse_arp(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        m = ARP_LINE.match(line.strip())
        if not m:
            continue
        try:
            mac = normalize_mac(m["mac"])
        except ValueError:
            continue
        if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or int(mac[:2], 16) & 1:
            continue  # broadcast/multicast entries
        rows.append({"ip": m["ip"], "mac": mac, "name": "" if m["name"] == "?" else m["name"]})
    return rows


def scan(resolve_names: bool = False, settle_s: float = 2.0) -> list[dict]:
    """Return devices currently on the LAN as [{ip, mac, name}]."""
    net = ipaddress.ip_network(f"{local_ipv4()}/24", strict=False)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for host in net.hosts():
            try:
                s.sendto(b"", (str(host), 9))  # the discard port; we only want the ARP lookup it triggers
            except OSError:
                pass
    time.sleep(settle_s)
    return parse_arp(macos.run(["arp", "-a" if resolve_names else "-an"], timeout=30))


def seen_macs(max_age_s: float = 45) -> set[str] | None:
    """One scan shared by every family on this server per scheduler tick; None if the scan failed."""
    if time.monotonic() - _cache["at"] > max_age_s:
        _cache["at"] = time.monotonic()
        try:
            _cache["seen"] = {d["mac"] for d in scan()}
            status.update(last_scan=utcnow(), devices=len(_cache["seen"]), error="")
        except Exception as exc:
            _cache["seen"] = None
            status.update(last_scan=utcnow(), devices=0, error=str(exc)[:200] or exc.__class__.__name__)
    return _cache["seen"]


def update(db: Session, family: Family, seen: set[str], now: datetime | None = None) -> list[str]:
    """Apply a scan to a family's members. Returns transitions like ["Sam arrived at Home"]."""
    home = geo.find_place(db, family, "Home")
    if home is None:
        return []
    now = now or utcnow()
    away_after = timedelta(minutes=int((family.settings or {}).get("wifi_away_minutes", 15)))
    changes = []
    for m in db.query(Member).filter_by(family_id=family.id):
        macs = set(m.wifi_macs or [])
        if not macs or not m.share_location:
            continue
        state = db.get(GeofenceState, (m.id, home.id))
        if macs & seen:
            m.wifi_last_seen = now
            db.commit()
            if (state is None or not state.inside) and geo.set_inside(db, family, m, home, True, state, "home Wi-Fi"):
                changes.append(f"{m.name} arrived at Home")
        elif state is not None and state.inside and m.wifi_last_seen and now - m.wifi_last_seen > away_after \
                and now - state.changed_at > away_after:
            # Also wait out the last change so a Shortcuts "arrived" isn't undone before Wi-Fi reconnects.
            if geo.set_inside(db, family, m, home, False, state, "home Wi-Fi"):
                changes.append(f"{m.name} left Home")
    return changes
