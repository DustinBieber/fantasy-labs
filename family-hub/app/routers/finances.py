import calendar
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Budget, Member, SavingsGoal, Transaction
from ..security import current_member, ensure_self_or_parent, get_owned, require_parent
from ..services.notify import raise_alert
from ..timeutil import local_today, utcnow
from .members import wallet_cents

router = APIRouter(prefix="/api/finance", tags=["finances"])


def tx_out(t: Transaction) -> dict:
    return {"id": t.id, "member_id": t.member_id, "amount_cents": t.amount_cents, "category": t.category,
            "description": t.description, "date": t.date.isoformat(), "goal_id": t.goal_id}


def _month_bounds(month: str | None, tz: str) -> tuple[dt.date, dt.date]:
    if month:
        try:
            y, m = map(int, month.split("-"))
            first = dt.date(y, m, 1)
        except ValueError:
            raise HTTPException(422, "month must look like 2026-10") from None
    else:
        first = local_today(tz).replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


@router.get("/transactions")
def list_transactions(month: str | None = None, member_id: int | None = None, household: bool = False,
                      me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if me.role != "parent":
        member_id, household = me.id, False
    first, last = _month_bounds(month, me.family.timezone)
    q = db.query(Transaction).filter(Transaction.family_id == me.family_id, Transaction.date >= first,
                                     Transaction.date <= last)
    if member_id is not None:
        q = q.filter(Transaction.member_id == member_id)
    elif household:
        q = q.filter(Transaction.member_id.is_(None))
    return [tx_out(t) for t in q.order_by(Transaction.date.desc(), Transaction.id.desc())]


class TxIn(BaseModel):
    amount_cents: int
    category: str = Field(default="general", min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    date: dt.date | None = None
    member_id: int | None = None  # set to record against a kid's wallet

    @field_validator("amount_cents")
    @classmethod
    def nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("amount can't be zero")
        return v


@router.post("/transactions")
def add_transaction(body: TxIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if me.role != "parent":
        # Kids can log their own spending from their wallet, but not add money.
        if body.member_id != me.id or body.amount_cents > 0:
            raise HTTPException(403, "Kids can only record their own spending")
        if wallet_cents(db, me.id) + body.amount_cents < 0:
            raise HTTPException(409, "Not enough money in your wallet")
    if body.member_id is not None:
        get_owned(db, Member, body.member_id, me.family_id)
    category = body.category.strip().lower()
    t = Transaction(family_id=me.family_id, member_id=body.member_id, amount_cents=body.amount_cents,
                    category=category, description=body.description,
                    date=body.date or local_today(me.family.timezone))
    db.add(t)
    db.commit()
    if t.member_id is None and t.amount_cents < 0:
        _check_budget(db, me, t)
    return tx_out(t)


def _check_budget(db: Session, me: Member, t: Transaction) -> None:
    budget = db.query(Budget).filter_by(family_id=me.family_id, category=t.category).first()
    if not budget:
        return
    first, last = _month_bounds(t.date.strftime("%Y-%m"), me.family.timezone)
    spent = -db.query(func.coalesce(func.sum(Transaction.amount_cents), 0)).filter(
        Transaction.family_id == me.family_id, Transaction.member_id.is_(None), Transaction.category == t.category,
        Transaction.amount_cents < 0, Transaction.date >= first, Transaction.date <= last).scalar()
    before = spent + t.amount_cents  # amount is negative
    limit = budget.monthly_limit_cents
    if before < limit <= spent:
        raise_alert(db, me.family, "finance", f"Over budget: {t.category}",
                    f"${spent / 100:,.2f} spent of ${limit / 100:,.2f} this month.", severity="warning",
                    audience=["parent"])
    elif before < 0.8 * limit <= spent:
        raise_alert(db, me.family, "finance", f"80% of {t.category} budget used",
                    f"${spent / 100:,.2f} of ${limit / 100:,.2f}.", severity="info", audience=["parent"])


@router.delete("/transactions/{tx_id}")
def delete_transaction(tx_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.delete(get_owned(db, Transaction, tx_id, me.family_id))
    db.commit()
    return {"ok": True}


@router.get("/summary")
def summary(month: str | None = None, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    first, last = _month_bounds(month, me.family.timezone)
    rows = db.query(Transaction.category, func.sum(Transaction.amount_cents)).filter(
        Transaction.family_id == me.family_id, Transaction.member_id.is_(None),
        Transaction.date >= first, Transaction.date <= last).group_by(Transaction.category).all()
    budgets = {b.category: b.monthly_limit_cents for b in db.query(Budget).filter_by(family_id=me.family_id)}
    income = sum(v for _, v in rows if v > 0)
    expenses = -sum(v for _, v in rows if v < 0)
    by_cat = {c: -v for c, v in rows if v < 0}
    categories = sorted(set(by_cat) | set(budgets))
    kids = db.query(Member).filter_by(family_id=me.family_id, role="child").all()
    return {
        "month": first.strftime("%Y-%m"), "income_cents": income, "expense_cents": expenses,
        "net_cents": income - expenses,
        "categories": [{"category": c, "spent_cents": by_cat.get(c, 0), "budget_cents": budgets.get(c)}
                       for c in categories],
        "wallets": [{"member_id": k.id, "name": k.name, "balance_cents": wallet_cents(db, k.id),
                     "weekly_allowance_cents": k.weekly_allowance_cents,
                     "goals": [goal_out(db, g) for g in _goals(db, me.family_id, k.id)]} for k in kids],
    }


@router.get("/wallet/{member_id}")
def wallet(member_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    ensure_self_or_parent(me, member_id)
    get_owned(db, Member, member_id, me.family_id)
    rows = db.query(Transaction).filter_by(member_id=member_id).order_by(Transaction.date.desc()).limit(100)
    return {"balance_cents": wallet_cents(db, member_id), "transactions": [tx_out(t) for t in rows]}


class BudgetIn(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    monthly_limit_cents: int = Field(gt=0)


@router.get("/budgets")
def list_budgets(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return [{"category": b.category, "monthly_limit_cents": b.monthly_limit_cents}
            for b in db.query(Budget).filter_by(family_id=me.family_id).order_by(Budget.category)]


@router.put("/budgets")
def set_budget(body: BudgetIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    category = body.category.strip().lower()
    b = db.query(Budget).filter_by(family_id=me.family_id, category=category).first()
    if b is None:
        b = Budget(family_id=me.family_id, category=category)
        db.add(b)
    b.monthly_limit_cents = body.monthly_limit_cents
    db.commit()
    return {"category": b.category, "monthly_limit_cents": b.monthly_limit_cents}


@router.delete("/budgets/{category}")
def delete_budget(category: str, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.query(Budget).filter_by(family_id=me.family_id, category=category.strip().lower()).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- savings goals
# Money moved into a goal leaves the wallet (Transaction with goal_id, negative amount); taking it out
# is a positive goal_id transaction. Parent gifts and matches add the money first, then move it in.

def goal_saved(db: Session, goal_id: int) -> int:
    return -db.query(func.coalesce(func.sum(Transaction.amount_cents), 0)).filter_by(goal_id=goal_id).scalar()


def goal_out(db: Session, g: SavingsGoal) -> dict:
    saved = goal_saved(db, g.id)
    return {"id": g.id, "member_id": g.member_id, "title": g.title, "icon": g.icon, "target_cents": g.target_cents,
            "saved_cents": saved, "match_percent": g.match_percent, "status": g.status,
            "percent": min(100, round(100 * saved / g.target_cents)) if g.target_cents else 0}


def _goals(db: Session, family_id: int, member_id: int | None = None) -> list[SavingsGoal]:
    q = db.query(SavingsGoal).filter(SavingsGoal.family_id == family_id, SavingsGoal.status != "closed")
    if member_id is not None:
        q = q.filter_by(member_id=member_id)
    return q.order_by(SavingsGoal.status, SavingsGoal.created_at).all()


@router.get("/goals")
def list_goals(member_id: int | None = None, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if me.role != "parent":
        member_id = me.id
    return [goal_out(db, g) for g in _goals(db, me.family_id, member_id)]


class GoalIn(BaseModel):
    member_id: int
    title: str = Field(min_length=1, max_length=120)
    icon: str = Field(default="🎯", max_length=16)
    target_cents: int = Field(gt=0, le=10_000_000)
    match_percent: int = Field(default=0, ge=0, le=200)


@router.post("/goals")
def create_goal(body: GoalIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    ensure_self_or_parent(me, body.member_id)
    get_owned(db, Member, body.member_id, me.family_id)
    g = SavingsGoal(family_id=me.family_id, member_id=body.member_id, title=body.title, icon=body.icon,
                    target_cents=body.target_cents, match_percent=body.match_percent if me.role == "parent" else 0)
    db.add(g)
    db.commit()
    if me.role != "parent":
        raise_alert(db, me.family, "finance", f"{me.name} started saving for {g.icon} {g.title}",
                    f"Goal: ${g.target_cents / 100:,.2f}. You can add a match on the Money tab.",
                    member_id=me.id, audience=["parent"])
    return goal_out(db, g)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=16)
    target_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    match_percent: int | None = Field(default=None, ge=0, le=200)


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalPatch, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    g = get_owned(db, SavingsGoal, goal_id, me.family_id)
    ensure_self_or_parent(me, g.member_id)
    data = body.model_dump(exclude_unset=True)
    if me.role != "parent" and "match_percent" in data:
        raise HTTPException(403, "Only parents can set a match")
    for k, v in data.items():
        setattr(g, k, v)
    _check_reached(db, me, g)
    db.commit()
    return goal_out(db, g)


class MoveIn(BaseModel):
    amount_cents: int = Field(gt=0, le=10_000_000)
    gift: bool = False  # parents only: add new money instead of moving it from the kid's wallet


def _check_reached(db: Session, me: Member, g: SavingsGoal) -> None:
    saved = goal_saved(db, g.id)
    if g.status == "active" and saved >= g.target_cents:
        g.status, g.reached_at = "reached", utcnow()
        db.commit()
        kid = db.get(Member, g.member_id)
        raise_alert(db, me.family, "finance", f"🎉 {kid.name} reached their goal: {g.icon} {g.title}!",
                    f"${saved / 100:,.2f} saved.", member_id=kid.id)
    elif g.status == "reached" and saved < g.target_cents:
        g.status, g.reached_at = "active", None


@router.post("/goals/{goal_id}/deposit")
def deposit(goal_id: int, body: MoveIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    g = get_owned(db, SavingsGoal, goal_id, me.family_id)
    ensure_self_or_parent(me, g.member_id)
    if g.status not in ("active", "reached"):
        raise HTTPException(409, "This goal is finished")
    today = local_today(me.family.timezone)
    amount = body.amount_cents

    def move_in(cents: int, what: str):
        db.add(Transaction(family_id=me.family_id, member_id=g.member_id, amount_cents=-cents, category="savings",
                           description=f"{what} → {g.title}", date=today, goal_id=g.id))

    def add_money(cents: int, category: str, what: str):
        db.add(Transaction(family_id=me.family_id, member_id=g.member_id, amount_cents=cents, category=category,
                           description=what, date=today))

    if body.gift:
        if me.role != "parent":
            raise HTTPException(403, "Only parents can gift money")
        add_money(amount, "gift", f"Gift for {g.title} from {me.name}")
        move_in(amount, "Gift")
        match = 0
    else:
        if wallet_cents(db, g.member_id) < amount:
            raise HTTPException(409, "Not enough money in the wallet")
        move_in(amount, "Saved")
        match = amount * g.match_percent // 100
        if match:
            add_money(match, "match", f"Parent match for {g.title}")
            move_in(match, "Match")
    db.commit()
    kid = db.get(Member, g.member_id)
    if not body.gift and me.role != "parent":
        extra = f" (+${match / 100:,.2f} match)" if match else ""
        raise_alert(db, me.family, "finance", f"{kid.name} saved ${amount / 100:,.2f} for {g.icon} {g.title}{extra}",
                    member_id=kid.id, audience=["parent"])
    _check_reached(db, me, g)
    db.commit()
    return goal_out(db, g)


@router.post("/goals/{goal_id}/withdraw")
def withdraw(goal_id: int, body: MoveIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    g = get_owned(db, SavingsGoal, goal_id, me.family_id)
    ensure_self_or_parent(me, g.member_id)
    if body.amount_cents > goal_saved(db, g.id):
        raise HTTPException(409, "There isn't that much saved")
    db.add(Transaction(family_id=me.family_id, member_id=g.member_id, amount_cents=body.amount_cents,
                       category="savings", description=f"Took out of {g.title}", date=local_today(me.family.timezone),
                       goal_id=g.id))
    db.commit()
    _check_reached(db, me, g)
    db.commit()
    return goal_out(db, g)


@router.post("/goals/{goal_id}/spend")
def spend_goal(goal_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    """Mark the goal bought: its saved money is spent (it already left the wallet)."""
    g = get_owned(db, SavingsGoal, goal_id, me.family_id)
    ensure_self_or_parent(me, g.member_id)
    if g.status != "reached" and me.role != "parent":
        raise HTTPException(409, "Keep saving! You can buy it once you reach the goal.")
    g.status = "spent"
    db.commit()
    kid = db.get(Member, g.member_id)
    raise_alert(db, me.family, "finance", f"{kid.name} bought {g.icon} {g.title} 🛍️",
                f"${goal_saved(db, g.id) / 100:,.2f} from savings.", member_id=kid.id, audience=["parent"])
    return goal_out(db, g)


@router.delete("/goals/{goal_id}")
def close_goal(goal_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    """Cancel a goal: any money still in it goes back to the wallet."""
    g = get_owned(db, SavingsGoal, goal_id, me.family_id)
    ensure_self_or_parent(me, g.member_id)
    saved = goal_saved(db, g.id)
    if saved > 0 and g.status != "spent":
        db.add(Transaction(family_id=me.family_id, member_id=g.member_id, amount_cents=saved, category="savings",
                           description=f"Closed goal {g.title}", date=local_today(me.family.timezone), goal_id=g.id))
    g.status = "closed"
    db.commit()
    return {"ok": True}
