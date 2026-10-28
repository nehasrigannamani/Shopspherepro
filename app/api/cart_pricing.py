from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.cart import Cart, CartItem
from app.models.user import User, UserRole
from app.schemas.cart_pricing import CartPriceLine, CartPriceSummary
from app.services.pricing import price_cart

router = APIRouter(prefix="/cart", tags=["Cart Price Calculation"])


@router.get("/summary", response_model=CartPriceSummary)
def calculate_cart_summary(
    shipping_method_id: int | None = Query(default=None, gt=0),
    coupon_code: str | None = Query(default=None, max_length=50),
    country: str = Query(default="IN", min_length=2, max_length=2),
    state: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full checkout preview using current server-side prices, promotions,
    coupon, tax and shipping rules. Nothing is changed or reserved."""
    if current_user.role != UserRole.CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only customers can view their cart pricing.",
        )

    cart = db.scalar(select(Cart).where(Cart.user_id == current_user.id))
    if cart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found."
        )

    cart_items = list(
        db.scalars(
            select(CartItem).where(CartItem.cart_id == cart.id).order_by(CartItem.id)
        ).all()
    )
    if not cart_items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Your cart is empty."
        )

    priced = price_cart(
        db,
        cart_items,
        shipping_method_id=shipping_method_id,
        coupon_code=coupon_code,
        country=country,
        region=state,
    )

    note = (
        "Final amounts are recalculated from live data at checkout."
        if priced.shipping_method
        else "Shipping is not included until a shipping_method_id is supplied."
    )

    return CartPriceSummary(
        cart_id=cart.id,
        currency=priced.currency,
        items=[
            CartPriceLine(
                product_id=line.product.id,
                variant_id=line.variant.id if line.variant else None,
                product_name=line.product.name,
                sku=line.variant.sku if line.variant else line.product.sku,
                quantity=line.cart_item.quantity,
                unit_price=line.unit_price,
                line_subtotal=line.line_subtotal,
                discount_amount=line.discount,
                tax_amount=line.tax,
                line_total=line.line_total,
            )
            for line in priced.lines
        ],
        item_count=priced.item_count,
        subtotal=priced.subtotal,
        promotion_discount=priced.promotion_discount_total,
        coupon_discount=priced.coupon_discount,
        discount_total=priced.discount_total,
        shipping_total=priced.shipping_total,
        tax_total=priced.tax_total,
        grand_total=priced.grand_total,
        coupon_code=priced.coupon.code if priced.coupon else None,
        shipping_method=(
            priced.shipping_method.name if priced.shipping_method else None
        ),
        pricing_note=note,
    )
