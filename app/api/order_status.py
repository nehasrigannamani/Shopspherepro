
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User, UserRole
from app.schemas.order_items import OrderStatusHistoryResponse
from app.services.notify import create_notification

router = APIRouter(tags=["Order Status"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
}

TRANSITIONS = {
    "PLACED": {"CONFIRMED", "CANCELLED"},
    "PENDING": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}


class OrderStatusUpdate(BaseModel):
    status: str = Field(min_length=3, max_length=30)
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions",
        )

    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = str(order.status).upper()
    new_status = payload.status.strip().upper()

    if new_status not in TRANSITIONS:
        raise HTTPException(
            status_code=422,
            detail="Unsupported order status",
        )

    if new_status == "CANCELLED":
        raise HTTPException(
            status_code=409,
            detail=(
                "Use POST /orders/{id}/cancel so stock is released and "
                "refunds are initiated"
            ),
        )

    if new_status not in TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid status transition: {old_status} -> {new_status}",
        )

    try:
        order.status = new_status

        db.add(
            OrderStatusHistory(
                order_id=order.id,
                previous_status=old_status,
                new_status=new_status,
                changed_by=current_user.id,
                note=payload.note,
            )
        )
        create_notification(
            db,
            order.user_id,
            f"Order {order.order_number} is {new_status.lower()}",
            f"Your order status changed from {old_status.lower()} to "
            f"{new_status.lower()}.",
            notification_type="ORDER",
            reference_type="order",
            reference_id=order.id,
            email=new_status in {"SHIPPED", "DELIVERED"},
            sms=new_status in {"SHIPPED", "DELIVERED"},
        )

        db.commit()
        db.refresh(order)

        return {
            "order_id": order.id,
            "order_number": order.order_number,
            "previous_status": old_status,
            "status": new_status,
        }

    except Exception:
        db.rollback()
        raise


@router.get(
    "/orders/{order_id}/status-history",
    response_model=list[OrderStatusHistoryResponse],
)
def get_order_status_history(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = db.query(Order).filter(Order.id == order_id).first()

    if order is None or (
        order.user_id != current_user.id
        and current_user.role not in STAFF_ROLES
    ):
        raise HTTPException(status_code=404, detail="Order not found")

    return (
        db.query(OrderStatusHistory)
        .filter(OrderStatusHistory.order_id == order_id)
        .order_by(
            OrderStatusHistory.created_at.asc(),
            OrderStatusHistory.id.asc(),
        )
        .all()
    )
