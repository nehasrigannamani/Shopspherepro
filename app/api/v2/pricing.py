from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.cart import Cart, CartItem
from app.models.coupon import Coupon
from app.models.user import User, UserRole
from app.schemas.pricing_loyalty import (
    AppliedPromotion, BreakdownCoupon, BreakdownLine, BreakdownLoyalty, CouponEligibility, PriceBreakdown,
)
from app.services import loyalty
from app.services.pricing import ZERO, coupon_problems, money, price_cart

router = APIRouter(prefix="/v2/pricing", tags=["Pricing (v2)"])


def _cart_items(db: Session, user: User) -> list[CartItem]:
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    items = []
    if cart is not None:
        items = list(db.scalars(select(CartItem).where(CartItem.cart_id == cart.id).order_by(CartItem.id)).all())
    if not items:
        raise HTTPException(status_code=409, detail="Your cart is empty")
    return items


@router.get("/breakdown", response_model=PriceBreakdown,
            summary="Final price breakdown for the cart: promotions, coupon, loyalty points, tax, shipping",
            description=("Line-by-line explanation of the final price using live server-side rules. Nothing is "
                         "changed or reserved. `redeem_points` previews spending loyalty points."),
            responses={400: {"description": "Coupon/points not usable"}, 409: {"description": "Empty cart / stock / points"}},
            dependencies=[Depends(rate_limit(60))])
def breakdown(
    shipping_method_id: int | None = Query(None, gt=0),
    coupon_code: str | None = Query(None, max_length=50),
    country: str = Query("IN", min_length=2, max_length=2),
    state: str | None = Query(None, max_length=100),
    redeem_points: int = Query(0, ge=0, le=10_000_000),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    priced = price_cart(db, _cart_items(db, user), shipping_method_id=shipping_method_id, coupon_code=coupon_code,
                        country=country, region=state, user_id=user.id, redeem_points=redeem_points)

    totals: dict[int, list] = {}
    lines = []
    for line in priced.lines:
        applied = []
        for promo, amount in line.applied:
            applied.append(AppliedPromotion(promotion_id=promo.id, name=promo.name, discount=amount))
            entry = totals.setdefault(promo.id, [promo, ZERO])
            entry[1] += amount
        lines.append(BreakdownLine(
            product_id=line.product.id, variant_id=line.variant.id if line.variant else None,
            name=line.product.name, sku=line.variant.sku if line.variant else line.product.sku,
            quantity=line.cart_item.quantity, unit_price=line.unit_price, line_subtotal=line.line_subtotal,
            promotions=applied, promotion_discount=line.promotion_discount, coupon_share=line.coupon_share,
            loyalty_share=line.loyalty_share, discount_total=line.discount, tax=line.tax, line_total=line.line_total,
        ))

    payable_before_points = money(sum((l.line_total + l.loyalty_share for l in priced.lines), ZERO))
    earn, _ = loyalty.compute_points(
        db, user.id, [(l.product.category_id, max(ZERO, l.line_subtotal - l.discount)) for l in priced.lines])
    notes = ["Final amounts are recalculated from live data at checkout."]
    if priced.shipping_method is None:
        notes.append("Shipping is not included until a shipping_method_id is supplied.")
    return PriceBreakdown(
        currency=priced.currency, lines=lines, subtotal=priced.subtotal,
        promotions=[AppliedPromotion(promotion_id=p.id, name=p.name, discount=money(a)) for p, a in totals.values()],
        promotion_discount=priced.promotion_discount_total,
        coupon=BreakdownCoupon(code=priced.coupon.code, discount=priced.coupon_discount) if priced.coupon else None,
        loyalty=BreakdownLoyalty(
            points_redeemed=priced.loyalty_points, discount=priced.loyalty_discount,
            available_points=loyalty.available_points(db, user.id),
            max_redeemable_points=loyalty.max_redeemable_points(db, user.id, payable_before_points),
            min_redeem_points=loyalty.settings.loyalty_min_redeem_points, estimated_points_to_earn=earn),
        discount_total=priced.discount_total, tax_total=priced.tax_total, shipping_total=priced.shipping_total,
        grand_total=priced.grand_total, note=" ".join(notes),
    )


@router.get("/coupon-eligibility", response_model=CouponEligibility,
            summary="Check whether the signed-in customer can use a coupon on the current cart",
            dependencies=[Depends(rate_limit(30))])
def coupon_eligibility(
    coupon_code: str = Query(..., min_length=1, max_length=50),
    shipping_method_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    code = coupon_code.strip().upper()
    coupon = db.scalar(select(Coupon).where(Coupon.code == code))
    if coupon is None:
        return CouponEligibility(code=code, eligible=False, reasons=["Coupon not found"])
    reasons = coupon_problems(db, coupon, user.id, datetime.now(timezone.utc))
    estimated = None
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    items = list(db.scalars(select(CartItem).where(CartItem.cart_id == cart.id)).all()) if cart else []
    if items:
        try:
            plain = price_cart(db, items, user_id=user.id, check_stock=False)
            if plain.subtotal < Decimal(coupon.min_order_amount):
                reasons.append(f"Coupon requires a minimum subtotal of {coupon.min_order_amount}")
            elif not reasons:
                with_coupon = price_cart(db, items, coupon_code=code, shipping_method_id=shipping_method_id,
                                         user_id=user.id, check_stock=False)
                estimated = with_coupon.coupon_discount
        except HTTPException as exc:
            reasons.append(str(exc.detail))
    return CouponEligibility(
        code=code, eligible=not reasons, reasons=reasons, min_order_amount=coupon.min_order_amount,
        max_discount=coupon.max_discount, estimated_discount=estimated if not reasons else None)
