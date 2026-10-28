from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.inventory import Inventory, InventoryMovement
from app.models.order import Order, OrderItem
from app.models.order_status_history import OrderStatusHistory
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.user import User, UserRole
from app.models.variant_inventory import (
    VariantInventory,
    VariantInventoryMovement,
)
from app.services.notify import create_notification, notify_roles
from app.services.payments import (
    PAID_STATUSES,
    RefundError,
    committed_refund_total,
    process_refund,
)

router = APIRouter(tags=["Order Cancellation"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
}

# Customers may cancel until the warehouse starts processing; staff can
# still cancel while an order is being processed.
CUSTOMER_CANCELLABLE = {"PLACED", "PENDING", "CONFIRMED"}
STAFF_CANCELLABLE = CUSTOMER_CANCELLABLE | {"PROCESSING"}


class OrderCancellationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    payload: OrderCancellationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_staff = current_user.role in STAFF_ROLES

    order = (
        db.query(Order).filter(Order.id == order_id).with_for_update().first()
    )

    if order is None or (order.user_id != current_user.id and not is_staff):
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = str(order.status).upper()

    if old_status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Order is already cancelled")

    allowed = STAFF_CANCELLABLE if is_staff else CUSTOMER_CANCELLABLE
    if old_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                "This order can no longer be cancelled; use the "
                "return/refund workflow"
                if old_status in {"SHIPPED", "DELIVERED", "RETURNED", "REFUNDED"}
                else f"Orders in status {old_status} cannot be cancelled by you"
            ),
        )

    payment_status = str(order.payment_status).upper()
    refund_id = None
    refund_note = None

    try:
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()

        # 1. Release stock held by the order (product- or variant-level).
        for item in items:
            reason = f"Order {order.order_number} cancelled: {payload.reason}"
            if item.variant_id is not None:
                stock = (
                    db.query(VariantInventory)
                    .filter(VariantInventory.variant_id == item.variant_id)
                    .with_for_update()
                    .first()
                )
                move = VariantInventoryMovement
                key = "variant_inventory_id"
            else:
                stock = (
                    db.query(Inventory)
                    .filter(Inventory.product_id == item.product_id)
                    .with_for_update()
                    .first()
                )
                move = InventoryMovement
                key = "inventory_id"

            if stock is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Inventory record is missing for product {item.product_id}",
                )

            stock.quantity_on_hand += item.quantity
            db.add(
                move(
                    **{key: stock.id},
                    quantity_change=item.quantity,
                    reason=reason[:255],
                    performed_by=current_user.id,
                )
            )
            item.fulfillment_status = "CANCELLED"

        # 2. Stop any unpaid gateway sessions for this order.
        pending = (
            db.query(Payment)
            .filter(Payment.order_id == order.id, Payment.status == "PENDING")
            .with_for_update()
            .all()
        )
        for payment in pending:
            payment.status = "CANCELLED"

        # 3. Queue a refund for money already received.
        paid_payment = (
            db.query(Payment)
            .filter(
                Payment.order_id == order.id,
                Payment.status.in_(PAID_STATUSES),
            )
            .with_for_update()
            .first()
        )
        if paid_payment is not None:
            remaining = Decimal(str(paid_payment.amount)) - committed_refund_total(
                db, paid_payment.id
            )
            if remaining > 0:
                refund = Refund(
                    payment_id=paid_payment.id,
                    order_id=order.id,
                    requested_by=current_user.id,
                    processed_by=current_user.id,
                    amount=remaining,
                    currency=paid_payment.currency,
                    reason=f"Order cancelled: {payload.reason}",
                    status="APPROVED",
                    decision_note="Auto-approved: cancellation before shipment",
                )
                db.add(refund)
                db.flush()
                refund_id = refund.id
        elif payment_status == "PAID":
            # e.g. cash already collected: no gateway payment to reverse.
            refund_note = "Payment was collected outside the gateway; finance must refund it manually"
            notify_roles(
                db,
                (UserRole.FINANCE_MANAGER,),
                "Manual refund required",
                f"Order {order.order_number} was cancelled after payment was "
                "collected outside the gateway.",
                notification_type="REFUND",
                reference_type="order",
                reference_id=order.id,
            )

        # 4. Status, history and notifications.
        order.status = "CANCELLED"
        db.add(
            OrderStatusHistory(
                order_id=order.id,
                previous_status=old_status,
                new_status="CANCELLED",
                changed_by=current_user.id,
                note=payload.reason,
            )
        )
        create_notification(
            db,
            order.user_id,
            f"Order {order.order_number} cancelled",
            "Your order was cancelled."
            + (" A refund has been started." if refund_id else ""),
            notification_type="ORDER",
            reference_type="order",
            reference_id=order.id,
            email=True,
        )

        db.commit()
        db.refresh(order)

    except Exception:
        db.rollback()
        raise

    refund_status = None
    if refund_id is not None:
        try:
            refund = process_refund(db, refund_id, actor_id=current_user.id)
            refund_status = refund.status
        except RefundError as exc:
            refund_status = "APPROVED"
            refund_note = f"Refund queued but not yet processed: {exc.detail}"
        except Exception:  # gateway outage: refund stays APPROVED for retry
            db.rollback()
            refund_status = "APPROVED"
            refund_note = "Refund queued; gateway processing will be retried"

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "previous_status": old_status,
        "status": order.status,
        "refund_id": refund_id,
        "refund_status": refund_status,
        "refund_note": refund_note,
        "message": "Order cancelled and inventory restored",
    }
