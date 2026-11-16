"""Promotion/coupon redemption records and the reversal of benefits when an order is cancelled."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.coupon import Coupon
from app.models.order import Order
from app.models.pricing_loyalty import PromotionRedemption
from app.models.promotion import Promotion
from app.services import loyalty
from app.services.pricing import ZERO, PricingResult, money


def record_redemptions(db: Session, order: Order, pricing: PricingResult, user_id: int) -> None:
    """Count one use per promotion/coupon per order and keep a redemption record of each.
    Caller holds the promotion/coupon row locks (price_cart(lock=True))."""
    totals: dict[int, list] = {}
    for line in pricing.lines:
        for promo, amount in line.applied:
            entry = totals.setdefault(promo.id, [promo, ZERO])
            entry[1] += amount
    for promo, amount in totals.values():
        promo.used_count += 1
        db.add(PromotionRedemption(order_id=order.id, user_id=user_id, promotion_id=promo.id,
                                   discount_amount=money(amount), status="APPLIED"))
    if pricing.coupon is not None:
        pricing.coupon.used_count += 1
        db.add(PromotionRedemption(order_id=order.id, user_id=user_id, coupon_id=pricing.coupon.id,
                                   discount_amount=money(pricing.coupon_discount), status="APPLIED"))


def reverse_redemptions(db: Session, order_id: int) -> int:
    """Mark the order's redemptions REVERSED and give the usage back (idempotent)."""
    from app.services.reservations import now_naive

    rows = db.scalars(select(PromotionRedemption).where(
        PromotionRedemption.order_id == order_id, PromotionRedemption.status == "APPLIED"
    ).with_for_update()).all()
    for row in rows:
        row.status, row.reversed_at = "REVERSED", now_naive()
        if row.promotion_id is not None:
            promo = db.scalar(select(Promotion).where(Promotion.id == row.promotion_id).with_for_update())
            if promo is not None:
                promo.used_count = max(0, promo.used_count - 1)
        if row.coupon_id is not None:
            coupon = db.scalar(select(Coupon).where(Coupon.id == row.coupon_id).with_for_update())
            if coupon is not None:
                coupon.used_count = max(0, coupon.used_count - 1)
    return len(rows)


def reverse_order_benefits(db: Session, order: Order) -> dict:
    """Called when a whole order is cancelled: usage back to promotions/coupons, points back to the customer."""
    return {
        "redemptions_reversed": reverse_redemptions(db, order.id),
        "points_returned": loyalty.return_redeemed_points(db, order),
    }
