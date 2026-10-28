
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.delivery import Delivery
from app.models.order import Order
from app.models.shipment_tracking import ShipmentTracking
from app.models.shipping_order import ShippingOrder
from app.models.user import User, UserRole
from app.schemas.deliveries import (
    DeliveryCreate,
    DeliveryResponse,
    DeliveryStatusUpdate,
)

router = APIRouter(prefix="/deliveries", tags=["Delivery Management"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.DELIVERY_MANAGER,
}

DELIVERY_TRANSITIONS = {
    "ASSIGNED": {"OUT_FOR_DELIVERY", "CANCELLED"},
    "OUT_FOR_DELIVERY": {"DELIVERED", "FAILED", "RESCHEDULED"},
    "RESCHEDULED": {"OUT_FOR_DELIVERY", "CANCELLED"},
    "FAILED": {"OUT_FOR_DELIVERY", "CANCELLED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}


def role_value(user: User) -> str:
    return str(getattr(user.role, "value", user.role)).upper()


def require_delivery_staff(user: User) -> None:
    if role_value(user) not in {role.value for role in STAFF_ROLES}:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to manage deliveries",
        )


def get_delivery_or_404(db: Session, delivery_id: int) -> Delivery:
    delivery = (
        db.query(Delivery)
        .filter(Delivery.id == delivery_id)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


def ensure_delivery_access(
    db: Session, delivery: Delivery, user: User
) -> None:
    if role_value(user) in {role.value for role in STAFF_ROLES}:
        return

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == delivery.shipping_order_id)
        .first()
    )
    if not shipping_order:
        raise HTTPException(status_code=404, detail="Shipment not found")

    order = (
        db.query(Order)
        .filter(Order.id == shipping_order.order_id)
        .first()
    )
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.post(
    "/shipping/{shipping_order_id}",
    response_model=DeliveryResponse,
    status_code=201,
)
def assign_delivery(
    shipping_order_id: int,
    payload: DeliveryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_delivery_staff(current_user)

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == shipping_order_id)
        .with_for_update()
        .first()
    )
    if not shipping_order:
        raise HTTPException(status_code=404, detail="Shipment not found")

    if shipping_order.status not in {"SHIPPED", "IN_TRANSIT"}:
        raise HTTPException(
            status_code=400,
            detail="Only shipped or in-transit orders can be assigned for delivery",
        )

    assignee = (
        db.query(User)
        .filter(User.id == payload.assigned_to)
        .first()
    )
    if not assignee or not assignee.is_active:
        raise HTTPException(
            status_code=400,
            detail="Assigned user does not exist or is inactive",
        )

    if role_value(assignee) != UserRole.DELIVERY_MANAGER.value:
        raise HTTPException(
            status_code=400,
            detail="The assigned user must have the DELIVERY_MANAGER role",
        )

    existing = (
        db.query(Delivery)
        .filter(Delivery.shipping_order_id == shipping_order_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A delivery record already exists for this shipment",
        )

    delivery = Delivery(
        shipping_order_id=shipping_order_id,
        assigned_to=payload.assigned_to,
        recipient_name=payload.recipient_name,
        delivery_notes=payload.delivery_notes,
        status="ASSIGNED",
    )

    try:
        db.add(delivery)
        db.commit()
        db.refresh(delivery)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A delivery record already exists for this shipment",
        )

    return delivery


@router.get("", response_model=list[DeliveryResponse])
def list_deliveries(
    status: str | None = Query(default=None, max_length=30),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Delivery)

    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        query = (
            query.join(
                ShippingOrder,
                Delivery.shipping_order_id == ShippingOrder.id,
            )
            .join(Order, ShippingOrder.order_id == Order.id)
            .filter(Order.user_id == current_user.id)
        )

    if status:
        query = query.filter(
            Delivery.status == status.strip().upper()
        )

    return (
        query.order_by(Delivery.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{delivery_id}", response_model=DeliveryResponse)
def get_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delivery = get_delivery_or_404(db, delivery_id)
    ensure_delivery_access(db, delivery, current_user)
    return delivery


@router.patch(
    "/{delivery_id}/status",
    response_model=DeliveryResponse,
)
def update_delivery_status(
    delivery_id: int,
    payload: DeliveryStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_delivery_staff(current_user)

    delivery = (
        db.query(Delivery)
        .filter(Delivery.id == delivery_id)
        .with_for_update()
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    new_status = payload.status.strip().upper()
    old_status = delivery.status.upper()

    if new_status not in DELIVERY_TRANSITIONS:
        raise HTTPException(
            status_code=400, detail="Unsupported delivery status"
        )

    if new_status not in DELIVERY_TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid delivery status transition: {old_status} to {new_status}",
        )

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == delivery.shipping_order_id)
        .with_for_update()
        .first()
    )
    if not shipping_order:
        raise HTTPException(status_code=404, detail="Shipment not found")

    now = datetime.now(timezone.utc)

    if new_status == "OUT_FOR_DELIVERY":
        if shipping_order.status not in {"SHIPPED", "IN_TRANSIT"}:
            raise HTTPException(
                status_code=400,
                detail="The shipment must be shipped or in transit before delivery",
            )
        if not shipping_order.tracking_number:
            raise HTTPException(
                status_code=400,
                detail="A tracking number is required before delivery",
            )
        shipping_order.status = "IN_TRANSIT"

    if new_status == "DELIVERED":
        delivery.delivered_at = now
        shipping_order.status = "DELIVERED"
        shipping_order.delivered_at = now

    if new_status == "FAILED":
        delivery.failure_reason = payload.failure_reason
        if not payload.failure_reason:
            raise HTTPException(
                status_code=400,
                detail="Provide a failure reason when delivery fails",
            )
        shipping_order.status = "DELIVERY_FAILED"

    if new_status == "RESCHEDULED":
        delivery.failure_reason = payload.failure_reason

    if new_status == "CANCELLED":
        shipping_order.status = "DELIVERY_FAILED"

    delivery.status = new_status

    if payload.recipient_name is not None:
        delivery.recipient_name = payload.recipient_name.strip()

    if payload.delivery_notes is not None:
        delivery.delivery_notes = payload.delivery_notes.strip()

    event = ShipmentTracking(
        shipping_order_id=shipping_order.id,
        status=new_status,
        location=None,
        description=(
            payload.delivery_notes.strip()
            if payload.delivery_notes
            else f"Delivery status updated to {new_status}"
        ),
        event_time=now,
        created_by=current_user.id,
    )
    db.add(event)

    try:
        db.commit()
        db.refresh(delivery)
    except Exception:
        db.rollback()
        raise

    return delivery
