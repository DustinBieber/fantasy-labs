"""Rule-based reading of order notes, tags and attributes: deadlines and keywords.

Handles the common ways people write a date ("need by Nov 3", "by 11/3",
"2026-11-03", "must arrive by Saturday"). The AI pass (services.ai) catches
whatever these rules miss.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}

_MONTH_RE = (r"(?:january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep"
             r"|october|oct|november|nov|december|dec)\.?")
_WEEKDAY_RE = (r"(?:monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat"
               r"|sunday|sun)\.?")
DATE_EXPR = (
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<num>\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    rf"|(?P<named>{_MONTH_RE}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)"
    rf"|(?P<dmy>\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}(?:,?\s+\d{{4}})?)"
    rf"|(?P<weekday>(?:this\s+|next\s+)?{_WEEKDAY_RE})"
    r"|(?P<rel>tomorrow|today|tonight)"
)
CUE = (r"(?:need(?:ed|s)?\s+(?:it\s+|this\s+|them\s+)?(?:by|before|on)|deliver(?:ed|y)?\s+(?:by|before|on)"
       r"|arriv(?:e|al)\s+(?:by|before|on)|must\s+(?:arrive|ship|deliver|be\s+delivered)\s+(?:by|before|on)"
       r"|ship\s+(?:by|before)|no\s+later\s+than|deadline(?:\s+is)?[:\s]|due(?:\s+(?:by|on))?|in[- ]hands?(?:\s+date)?[:\s]"
       r"|event\s+(?:date|is\s+on|on)[:\s]?|before|by)")
DEADLINE_RE = re.compile(rf"\b{CUE}\s*(?:the\s+)?(?:{DATE_EXPR})(?![a-z0-9])", re.IGNORECASE)
STANDALONE_RE = re.compile(rf"^\s*(?:{DATE_EXPR})\s*$", re.IGNORECASE)
TAG_RE = re.compile(r"^(?:need[-_ ]?by|deadline|due|ship[-_ ]?by|deliver[-_ ]?by|in[-_ ]?hands)[:=_\s-]*(.+)$",
                    re.IGNORECASE)

PRIORITY_TAGS = {"priority", "rush", "expedite", "expedited", "vip", "urgent"}
SUBSCRIPTION_TAGS = {"subscription", "subscription recurring order", "recurring", "standing order"}
SEASONAL_TAGS = {"seasonal", "preorder", "pre-order", "holiday"}
# Note keywords that mean the order may be wrong or about to change, not just "handle with care".
HIGH_RISK_KEYWORDS = {"cancel", "hold", "address change", "change address", "wrong address", "damaged", "do not"}


def _year_for(month: int, day: int, ref: date) -> date | None:
    """Dates without a year mean the next occurrence; a date up to 60 days back is read as overdue."""
    try:
        candidate = date(ref.year, month, day)
    except ValueError:
        return None
    if candidate < ref - timedelta(days=60):
        try:
            candidate = date(ref.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def _parse_match(m: re.Match, ref: date) -> date | None:
    g = m.groupdict()
    try:
        if g.get("iso"):
            return date.fromisoformat(g["iso"])
        if g.get("num"):
            parts = [int(p) for p in g["num"].split("/")]
            if len(parts) == 3:
                year = parts[2] + 2000 if parts[2] < 100 else parts[2]
                return date(year, parts[0], parts[1])
            return _year_for(parts[0], parts[1], ref)
        if g.get("named") or g.get("dmy"):
            text = (g.get("named") or g.get("dmy")).lower().replace(",", " ").replace(".", " ")
            month = next(MONTHS[w[:3]] for w in text.split() if w[:3] in MONTHS and not w[0].isdigit())
            nums = [int(re.sub(r"\D", "", w)) for w in text.split() if w[0].isdigit()]
            day = nums[0]
            year = next((n for n in nums[1:] if n > 1900), None)
            return date(year, month, day) if year else _year_for(month, day, ref)
        if g.get("weekday"):
            wd = WEEKDAYS[g["weekday"].lower().rstrip(".").split()[-1][:3]]
            ahead = (wd - ref.weekday()) % 7 or 7
            return ref + timedelta(days=ahead)
        if g.get("rel"):
            return ref + timedelta(days=1 if g["rel"].lower() == "tomorrow" else 0)
    except (ValueError, StopIteration, IndexError):
        return None
    return None


def parse_date_value(value: str, ref: date) -> date | None:
    """A value that should be just a date (attribute, tag suffix)."""
    if not value:
        return None
    m = STANDALONE_RE.match(value.strip())
    if m:
        return _parse_match(m, ref)
    if "T" in value:  # ISO timestamp
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def find_deadline(text: str | None, ref: date) -> tuple[date, str] | None:
    """Earliest date in ``text`` introduced by a deadline cue ("need by", "before", ...)."""
    if not text:
        return None
    found = []
    for m in DEADLINE_RE.finditer(text):
        d = _parse_match(m, ref)
        if d:
            found.append((d, m.group(0).strip()))
    return min(found) if found else None


def keyword_hits(text: str | None, keywords: list[str]) -> list[str]:
    if not text:
        return []
    low = text.lower().replace("’", "'")
    return [k for k in keywords if re.search(rf"(?<![a-z]){re.escape(k.lower())}(?![a-z])", low)]


@dataclass
class OrderSignals:
    deadline: date | None = None
    deadline_source: str | None = None
    deadline_text: str | None = None
    flags: list[str] = field(default_factory=list)
    order_type: str = "standard"


def read_order(*, notes: str | None, tags: list[str], attributes: dict[str, str], ref: date,
               keywords: list[str], deadline_keys: list[str], source_type: str | None = None,
               has_seasonal_items: bool = False) -> OrderSignals:
    sig = OrderSignals()
    candidates: list[tuple[date, str, str]] = []
    for key, value in attributes.items():
        if any(k in key.lower() for k in deadline_keys):
            d = parse_date_value(str(value), ref)
            if d:
                candidates.append((d, "attribute", f"{key}: {value}"))
    for tag in tags:
        m = TAG_RE.match(tag.strip())
        if m:
            d = parse_date_value(m.group(1), ref)
            if d:
                candidates.append((d, "tag", tag))
    hit = find_deadline(notes, ref)
    if hit:
        candidates.append((hit[0], "note", hit[1]))
    if candidates:
        sig.deadline, sig.deadline_source, sig.deadline_text = min(candidates)

    sig.flags = keyword_hits(notes, keywords)
    low_tags = {t.strip().lower() for t in tags}
    if low_tags & PRIORITY_TAGS:
        sig.order_type = "priority"
    elif low_tags & SUBSCRIPTION_TAGS:
        sig.order_type = "subscription"
    elif source_type:
        sig.order_type = source_type
    elif low_tags & SEASONAL_TAGS or has_seasonal_items:
        sig.order_type = "seasonal"
    return sig


def deadline_datetime(d: date, tz: str) -> datetime:
    from ..timeutil import end_of_day_utc

    return end_of_day_utc(d, tz)
