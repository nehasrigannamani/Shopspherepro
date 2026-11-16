"""Loyalty points: append-only ledger with FIFO lots.

* ``loyalty_ledger.points`` is signed and the balance is its sum.
* Every positive entry (EARN, REVERSE_REDEEM, ADJUST+) is a *lot* that remembers how many points
  are still spendable (``points_remaining``) and when they expire. Redeeming spends the lot that
  expires first; expiry writes an EXPIRE entry for whatever is left in a lot.
* Every state-changing call carries an idempotency key, so retries and replays never double-credit.
Functions add/flush only; callers commit.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order, OrderItem
from app.models.pricing_loyalty import CustomerSegmentMember, LoyaltyLedger, LoyaltyRule
from app.models.product import Product
from app.services.pricing import money
from app.services.reservations import now_naive

EARN_ELIGIBLE_PAYMENT = {"PAID", "PARTIALLY_REFUNDED"}


def _floor(value: Decimal) -> int:
    return int(Decimal(value).to_integral_value(rounding=ROUND_FLOOR))


# ------------------------------------------------------------------ ledger primitives
def _append(db: Session, *, user_id: int, entry_type: str, points: int, key: str | None = None,
            order_id: int | None = None, reason: str | None = None, details: dict | None = None,
            created_by: int | None = None, lot_expires_at=None, make_lot: bool = False):
    """Add one entry. Returns (row, created). A repeated key returns the original row."""
    if key:
        existing = db.scalar(select(LoyaltyLedger).where(LoyaltyLedger.idempotency_key == key))
        if existing is not None:
            return existing, False
    row = LoyaltyLedger(
        user_id=user_id, entry_type=entry_type, points=points,
        points_remaining=points if (make_lot and points > 0) else 0,
        expires_at=lot_expires_at if make_lot else None,
        order_id=order_id, idempotency_key=key, reason=(reason or "")[:500] or None,
        details=details, created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row, True


def lot_expiry(expiry_days: int | None):
    days = settings.loyalty_default_expiry_days if expiry_days is None else expiry_days
    return None if not days else now_naive() + timedelta(days=days)


def balance(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LoyaltyLedger.points), 0)).where(
        LoyaltyLedger.user_id == user_id)) or 0)


def _live_lot_filter(now):
    return (LoyaltyLedger.points_remaining > 0,
            or_(LoyaltyLedger.expires_at.is_(None), LoyaltyLedger.expires_at > now))


def available_points(db: Session, user_id: int) -> int:
    """Spendable points: remaining points in lots that have not expired."""
    return int(db.scalar(select(func.coalesce(func.sum(LoyaltyLedger.points_remaining), 0)).where(
        LoyaltyLedger.user_id == user_id, *_live_lot_filter(now_naive()))) or 0)


def expiring_soon(db: Session, user_id: int, days: int = 30) -> int:
    now = now_naive()
    return int(db.scalar(select(func.coalesce(func.sum(LoyaltyLedger.points_remaining), 0)).where(
        LoyaltyLedger.user_id == user_id, LoyaltyLedger.points_remaining > 0,
        LoyaltyLedger.expires_at.is_not(None), LoyaltyLedger.expires_at > now,
        LoyaltyLedger.expires_at <= now + timedelta(days=days))) or 0)


def expire_due(db: Session, user_id: int | None = None, limit: int = 500) -> int:
    """Write EXPIRE entries for lots past their expiry. Safe to repeat."""
    stmt = select(LoyaltyLedger).where(
        LoyaltyLedger.points_remaining > 0, LoyaltyLedger.expires_at.is_not(None),
        LoyaltyLedger.expires_at <= now_naive())
    if user_id is not None:
        stmt = stmt.where(LoyaltyLedger.user_id == user_id)
    lots = list(db.scalars(stmt.order_by(LoyaltyLedger.id).limit(limit).with_for_update()).all())
    for lot in lots:
        _append(db, user_id=lot.user_id, entry_type="EXPIRE", points=-lot.points_remaining,
                key=f"expire:{lot.id}", reason="Points expired", details={"lot_id": lot.id})
        lot.points_remaining = 0
    db.flush()
    return len(lots)


def _spend(db: Session, user_id: int, points: int) -> None:
    """Take ``points`` from live lots, earliest expiry first. Locks the lots."""
    lots = db.scalars(
        select(LoyaltyLedger).where(LoyaltyLedger.user_id == user_id, *_live_lot_filter(now_naive()))
        .order_by(LoyaltyLedger.expires_at.is_(None), LoyaltyLedger.expires_at, LoyaltyLedger.id)
        .with_for_update()
    ).all()
    need = points
    for lot in lots:
        if need == 0:
            break
        take = min(lot.points_remaining, need)
        lot.points_remaining -= take
        need -= take
    if need > 0:
        raise HTTPException(status_code=409, detail="Insufficient loyalty points")


def redeem(db: Session, user_id: int, points: int, order_id: int) -> LoyaltyLedger:
    """Spend points for an order (called inside checkout, under its transaction)."""
    expire_due(db, user_id)
    key = f"redeem:{order_id}"
    existing = db.scalar(select(LoyaltyLedger).where(LoyaltyLedger.idempotency_key == key))
    if existing is not None:
        return existing
    _spend(db, user_id, points)
    row, _ = _append(db, user_id=user_id, entry_type="REDEEM", points=-points, key=key, order_id=order_id,
                     reason=f"Redeemed on order {order_id}")
    return row


def adjust(db: Session, user_id: int, points: int, reason: str, actor_id: int, key: str | None = None):
    """Manual correction by staff (positive creates a lot, negative spends lots)."""
    if points == 0:
        raise HTTPException(status_code=422, detail="Adjustment must not be zero")
    if key:
        existing = db.scalar(select(LoyaltyLedger).where(LoyaltyLedger.idempotency_key == key))
        if existing is not None:
            return existing, False
    if points < 0:
        expire_due(db, user_id)
        _spend(db, user_id, -points)
    return _append(db, user_id=user_id, entry_type="ADJUST", points=points, key=key, reason=reason,
                   created_by=actor_id, make_lot=True, lot_expires_at=lot_expiry(None))


# ------------------------------------------------------------------ earning
def quote_redemption(db: Session, user_id: int, points: int, payable: Decimal) -> Decimal:
    """Validate a redemption request against balance and caps; returns its currency value."""
    if points < settings.loyalty_min_redeem_points:
        raise HTTPException(status_code=400, detail=f"Minimum redemption is {settings.loyalty_min_redeem_points} points")
    expire_due(db, user_id)
    if available_points(db, user_id) < points:
        raise HTTPException(status_code=409, detail="Insufficient loyalty points")
    value = Decimal(str(settings.loyalty_point_value))
    discount = money(Decimal(points) * value)
    cap = money(Decimal(payable) * Decimal(settings.loyalty_max_redeem_percent) / 100)
    if discount > cap:
        raise HTTPException(status_code=409,
                            detail=f"At most {_floor(cap / value)} points can be used on this order")
    return discount


def max_redeemable_points(db: Session, user_id: int, payable: Decimal) -> int:
    value = Decimal(str(settings.loyalty_point_value))
    cap = money(Decimal(payable) * Decimal(settings.loyalty_max_redeem_percent) / 100)
    by_cap = _floor(cap / value) if value > 0 else 0
    best = min(by_cap, available_points(db, user_id))
    return best if best >= settings.loyalty_min_redeem_points else 0


def compute_points(db: Session, user_id: int, lines: list[tuple[int | None, Decimal]]):
    """lines = [(category_id, net_amount)]. Returns (points, rule). The highest-priority
    matching rule wins; a category rule counts only its category's spend."""
    now = now_naive()
    member_of = set(db.scalars(select(CustomerSegmentMember.segment_id).where(
        CustomerSegmentMember.user_id == user_id)).all())
    rules = db.scalars(select(LoyaltyRule).where(
        LoyaltyRule.is_active.is_(True),
        or_(LoyaltyRule.starts_at.is_(None), LoyaltyRule.starts_at <= now),
        or_(LoyaltyRule.ends_at.is_(None), LoyaltyRule.ends_at > now),
    ).order_by(LoyaltyRule.priority.desc(), LoyaltyRule.earn_rate.desc(), LoyaltyRule.id.desc())).all()
    for rule in rules:
        if rule.segment_id is not None and rule.segment_id not in member_of:
            continue
        base = sum((amt for cat, amt in lines if rule.category_id is None or cat == rule.category_id), Decimal("0"))
        if base <= 0 or base < Decimal(str(rule.min_order_value)):
            continue
        points = _floor(base * Decimal(str(rule.earn_rate)))
        if rule.max_points_per_order is not None:
            points = min(points, rule.max_points_per_order)
        if points > 0:
            return points, rule
    return 0, None


def order_net_lines(db: Session, order: Order) -> list[tuple[int | None, Decimal]]:
    """Spend that earns points: price x kept units, minus the discount share, before tax."""
    lines = []
    for item in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all():
        kept = item.quantity - item.quantity_cancelled
        if kept <= 0 or item.quantity <= 0:
            continue
        net = Decimal(item.unit_price) * kept - Decimal(item.discount_amount) * kept / item.quantity
        product = db.get(Product, item.product_id)
        lines.append((product.category_id if product else None, max(Decimal("0"), net)))
    return lines


def credit_order_points(db: Session, order: Order, actor_id: int | None = None) -> int:
    """Credit points once for a delivered, paid order. Returns points credited (0 if none/already)."""
    if str(order.status).upper() != "DELIVERED" or str(order.payment_status).upper() not in EARN_ELIGIBLE_PAYMENT:
        return 0
    key = f"earn:{order.id}"
    if db.scalar(select(LoyaltyLedger.id).where(LoyaltyLedger.idempotency_key == key)) is not None:
        return 0
    points, rule = compute_points(db, order.user_id, order_net_lines(db, order))
    if points <= 0:
        return 0
    _append(db, user_id=order.user_id, entry_type="EARN", points=points, key=key, order_id=order.id,
            reason=f"Order {order.order_number}", details={"rule_id": rule.id, "rule": rule.name},
            created_by=actor_id, make_lot=True, lot_expires_at=lot_expiry(rule.expiry_days))
    return points


# ------------------------------------------------------------------ reversals
def return_redeemed_points(db: Session, order: Order) -> int:
    """Give back points spent on an order that was cancelled."""
    spent = order.loyalty_points_redeemed or 0
    if spent <= 0:
        return 0
    _, created = _append(db, user_id=order.user_id, entry_type="REVERSE_REDEEM", points=spent,
                         key=f"return-redeem:{order.id}", order_id=order.id,
                         reason=f"Order {order.order_number} cancelled", make_lot=True,
                         lot_expires_at=lot_expiry(None))
    return spent if created else 0


def reverse_earned_for_refund(db: Session, order: Order, refunded_total: Decimal, paid_total: Decimal) -> int:
    """Take back earned points in proportion to the refunded share of the payment.
    Only points still unspent can be taken back; a shortfall is noted, never turned into a debt."""
    earn = db.scalar(select(LoyaltyLedger).where(LoyaltyLedger.idempotency_key == f"earn:{order.id}").with_for_update())
    if earn is None or paid_total <= 0:
        return 0
    share = min(Decimal("1"), Decimal(refunded_total) / Decimal(paid_total))
    target = _floor(Decimal(earn.points) * share)
    already = -int(db.scalar(select(func.coalesce(func.sum(LoyaltyLedger.points), 0)).where(
        LoyaltyLedger.order_id == order.id, LoyaltyLedger.entry_type == "REVERSE_EARN")) or 0)
    delta = target - already
    if delta <= 0:
        return 0
    take = min(earn.points_remaining, delta)
    if take <= 0:
        return 0
    earn.points_remaining -= take
    _append(db, user_id=order.user_id, entry_type="REVERSE_EARN", points=-take,
            key=f"reverse-earn:{order.id}:{int(Decimal(refunded_total) * 100)}", order_id=order.id,
            reason=f"Refund on order {order.order_number}",
            details={"requested": delta, "reversed": take, "unrecoverable": delta - take})
    return take
