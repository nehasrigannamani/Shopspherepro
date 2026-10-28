
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.user import User, UserRole
from app.schemas.order_items import OrderItemResponse

router = APIRouter(tags=["Order Items"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.FINANCE_MANAGER,
    UserRole.SUPPORT_AGENT,
}


def get_accessible_order(
    db: Session, order_id: int, user: User
) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    if (
        order.user_id != user.id
        and user.role not in STAFF_ROLES
    ):
        # Do not reveal whether another customer's order exists.
        raise HTTPException(status_code=404, detail="Order not found")

    return order


@router.get(
    "/orders/{order_id}/items",
    response_model=list[OrderItemResponse],
)
def list_order_items(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_accessible_order(db, order_id, current_user)

    return (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order_id)
        .order_by(OrderItem.id.asc())
        .all()
    )


@router.get(
    "/orders/{order_id}/items/{item_id}",
    response_model=OrderItemResponse,
)
def get_order_item(
    order_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_accessible_order(db, order_id, current_user)

    item = (
        db.query(OrderItem)
        .filter(
            OrderItem.id == item_id,
            OrderItem.order_id == order_id,
        )
        .first()
    )

    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Order item not found",
        )

    return item


FULFILLMENT_STATUSES = {
    "PENDING", "PACKED", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED",
}
FULFILLMENT_STAFF = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.DELIVERY_MANAGER,
}
FULFILLMENT_TRANSITIONS = {
    "PENDING": {"PACKED", "CANCELLED"},
    "PACKED": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"DELIVERED", "RETURNED"},
    "DELIVERED": {"RETURNED"},
    "CANCELLED": set(),
    "RETURNED": set(),
}


class FulfillmentUpdate(BaseModel):
    status: str = Field(min_length=3, max_length=30)


@router.patch(
    "/orders/{order_id}/items/{item_id}/fulfillment",
    response_model=OrderItemResponse,
)
def update_item_fulfillment(
    order_id: int,
    item_id: int,
    payload: FulfillmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in FULFILLMENT_STAFF:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    item = (
        db.query(OrderItem)
        .filter(OrderItem.id == item_id, OrderItem.order_id == order_id)
        .with_for_update()
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Order item not found")

    new_status = payload.status.strip().upper()
    if new_status not in FULFILLMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported fulfillment status")
    if new_status not in FULFILLMENT_TRANSITIONS.get(item.fulfillment_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid transition: {item.fulfillment_status} -> {new_status}",
        )

    item.fulfillment_status = new_status
    db.commit()
    db.refresh(item)
    return item
