from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.account import Address
from app.models.cart import Cart, CartItem
from app.models.inventory import InventoryMovement
from app.models.order import Order, OrderItem
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User, UserRole
from app.models.variant_inventory import VariantInventoryMovement
from app.schemas.order import CheckoutRequest, OrderDetailResponse
from app.services.notify import create_notification, notify_roles
from app.services.pricing import (  # noqa: F401  (re-exported for callers)
    calculate_tax,
    money,
    price_cart,
    utc_value,
)

router = APIRouter(prefix="/checkout", tags=["Checkout Management"])


def _load_address(
    db: Session, address_id: int, user: User, kinds: tuple[str, ...], label: str
) -> Address:
    address = db.get(Address, address_id)
    if address is None or address.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"{label} address not found")
    if address.address_type not in kinds:
        raise HTTPException(
            status_code=409, detail=f"Address {address_id} cannot be used as a {label.lower()} address"
        )
    return address


def resolve_shipping(db: Session, payload: CheckoutRequest, user: User) -> dict:
    """Return the shipping address snapshot for the order."""
    if payload.shipping_address_id is not None:
        address = _load_address(
            db, payload.shipping_address_id, user, ("SHIPPING", "BOTH"), "Shipping"
        )
        return {
            "address_id": address.id,
            "full_name": address.full_name,
            "phone": address.phone,
            "line1": address.line1,
            "line2": address.line2,
            "city": address.city,
            "state": address.state,
            "postal_code": address.postal_code,
            "country": address.country.upper(),
        }
    return {
        "address_id": None,
        "full_name": payload.shipping_full_name,
        "phone": payload.shipping_phone,
        "line1": payload.shipping_address_line1,
        "line2": payload.shipping_address_line2,
        "city": payload.shipping_city,
        "state": payload.shipping_state,
        "postal_code": payload.shipping_postal_code,
        "country": payload.shipping_country.upper(),
    }


@router.post("", response_model=OrderDetailResponse, status_code=201)
def checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.CUSTOMER:
        raise HTTPException(status_code=403, detail="Only customers can check out")

    try:
        # Locking the cart serialises duplicate/concurrent checkout calls: the
        # second one finds an empty cart and is rejected.
        cart = db.scalar(
            select(Cart).where(Cart.user_id == current_user.id).with_for_update()
        )
        if cart is None:
            raise HTTPException(status_code=404, detail="Cart not found")

        cart_items = list(
            db.scalars(
                select(CartItem)
                .where(CartItem.cart_id == cart.id)
                .order_by(CartItem.id)
                .with_for_update()
            ).all()
        )
        if not cart_items:
            raise HTTPException(status_code=409, detail="Cart is empty")

        ship = resolve_shipping(db, payload, current_user)

        billing_address_id = None
        if payload.billing_address_id is not None:
            billing_address_id = _load_address(
                db, payload.billing_address_id, current_user,
                ("BILLING", "BOTH"), "Billing",
            ).id
        elif ship["address_id"] is not None:
            shipping_row = db.get(Address, ship["address_id"])
            if shipping_row.address_type == "BOTH":
                billing_address_id = shipping_row.id

        pricing = price_cart(
            db,
            cart_items,
            shipping_method_id=payload.shipping_method_id,
            coupon_code=payload.coupon_code,
            country=ship["country"],
            region=ship["state"],
            lock=True,
        )

        order = Order(
            order_number=f"SS-{uuid4().hex[:12].upper()}",
            user_id=current_user.id,
            status="PLACED",
            payment_status="PENDING",
            currency=pricing.currency,
            subtotal=pricing.subtotal,
            discount_total=pricing.discount_total,
            shipping_total=pricing.shipping_total,
            tax_total=pricing.tax_total,
            grand_total=pricing.grand_total,
            coupon_code=pricing.coupon.code if pricing.coupon else None,
            shipping_method_name=pricing.shipping_method.name,
            shipping_address_id=ship["address_id"],
            billing_address_id=billing_address_id,
            shipping_full_name=ship["full_name"],
            shipping_phone=ship["phone"],
            shipping_address_line1=ship["line1"],
            shipping_address_line2=ship["line2"],
            shipping_city=ship["city"],
            shipping_state=ship["state"],
            shipping_postal_code=ship["postal_code"],
            shipping_country=ship["country"],
            notes=payload.notes,
        )
        db.add(order)
        db.flush()

        low_stock_alerts = []
        for line in pricing.lines:
            quantity = line.cart_item.quantity
            variant = line.variant

            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=line.product.id,
                    variant_id=variant.id if variant else None,
                    variant_sku=variant.sku if variant else None,
                    variant_options=variant.option_values if variant else None,
                    product_name=line.product.name,
                    product_sku=line.product.sku,
                    product_description=line.product.description,
                    quantity=quantity,
                    unit_price=line.unit_price,
                    discount_amount=line.discount,
                    tax_amount=line.tax,
                    line_total=line.line_total,
                    fulfillment_status="PENDING",
                )
            )

            # Stock is deducted while the row lock is held, and a movement
            # record keeps the audit trail of every change.
            stock = line.stock
            before = stock.quantity_on_hand
            stock.quantity_on_hand = before - quantity
            reason = f"Order {order.order_number} placed"

            if variant is not None:
                db.add(
                    VariantInventoryMovement(
                        variant_inventory_id=stock.id,
                        quantity_change=-quantity,
                        reason=reason,
                        performed_by=current_user.id,
                    )
                )
            else:
                db.add(
                    InventoryMovement(
                        inventory_id=stock.id,
                        quantity_change=-quantity,
                        reason=reason,
                        performed_by=current_user.id,
                    )
                )

            if before > stock.low_stock_threshold >= stock.quantity_on_hand:
                low_stock_alerts.append(
                    (line.product, variant, stock.quantity_on_hand, stock.low_stock_threshold)
                )

            if line.promotion is not None:
                line.promotion.used_count += 1

        if pricing.coupon is not None:
            pricing.coupon.used_count += 1

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                previous_status=None,
                new_status="PLACED",
                changed_by=current_user.id,
                note="Order placed by customer",
            )
        )

        for item in cart_items:
            db.delete(item)

        create_notification(
            db,
            current_user.id,
            f"Order {order.order_number} placed",
            f"Thank you! Your order total is {order.grand_total} "
            f"{order.currency}. Complete payment to confirm it.",
            notification_type="ORDER",
            reference_type="order",
            reference_id=order.id,
            email=True,
            email_subject=f"Order confirmation {order.order_number}",
        )
        for product, variant, remaining, threshold in low_stock_alerts:
            label = f"{product.sku}" + (f" / {variant.sku}" if variant else "")
            notify_roles(
                db,
                (UserRole.INVENTORY_MANAGER,),
                "Low stock alert",
                f"{label} is down to {remaining} (threshold {threshold}). "
                "Consider restocking.",
                notification_type="INVENTORY",
                reference_type="product",
                reference_id=product.id,
            )

        db.commit()
        db.refresh(order)

        items = list(
            db.scalars(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
            ).all()
        )
        return OrderDetailResponse(
            **{c.name: getattr(order, c.name) for c in Order.__table__.columns},
            items=items,
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
