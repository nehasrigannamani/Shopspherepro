
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.shipping_order import ShippingOrder
from app.models.shipment_tracking import ShipmentTracking
from app.models.user import User, UserRole
from app.schemas.shipment_tracking import (
    ShipmentTrackingCreate,
    ShipmentTrackingResponse,
)

router = APIRouter(
    prefix="/shipping/tracking",
    tags=["Shipment Tracking"],
)

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.DELIVERY_MANAGER,
}


def role_value(user: User) -> str:
    return str(getattr(user.role, "value", user.role)).upper()


def get_shipping_order_or_404(
    db: Session, shipping_order_id: int
) -> ShippingOrder:
    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == shipping_order_id)
        .first()
    )

    if not shipping_order:
        raise HTTPException(
            status_code=404,
            detail="Shipping order not found",
        )

    return shipping_order


def ensure_access(
    db: Session,
    shipping_order: ShippingOrder,
    user: User,
) -> None:
    if role_value(user) in {role.value for role in STAFF_ROLES}:
        return

    order = (
        db.query(Order)
        .filter(Order.id == shipping_order.order_id)
        .first()
    )

    if not order or order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.post(
    "/{shipping_order_id}/events",
    response_model=ShipmentTrackingResponse,
    status_code=201,
)
def create_tracking_event(
    shipping_order_id: int,
    payload: ShipmentTrackingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        raise HTTPException(
            status_code=403,
            detail="Only authorized staff can add tracking events",
        )

    shipping_order = get_shipping_order_or_404(
        db, shipping_order_id
    )

    if shipping_order.status in {"CANCELLED", "RETURNED"}:
        raise HTTPException(
            status_code=400,
            detail="Tracking events cannot be added to cancelled or returned shipments",
        )

    event_time = payload.event_time or datetime.now(timezone.utc)

    if event_time > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="Tracking event time cannot be in the future",
        )

    event = ShipmentTracking(
        shipping_order_id=shipping_order_id,
        status=payload.status.strip().upper(),
        location=(
            payload.location.strip()
            if payload.location and payload.location.strip()
            else None
        ),
        description=payload.description.strip(),
        event_time=event_time,
        created_by=current_user.id,
    )

    db.add(event)

    try:
        db.commit()
        db.refresh(event)
    except Exception:
        db.rollback()
        raise

    return event


@router.get(
    "/{shipping_order_id}/events",
    response_model=list[ShipmentTrackingResponse],
)
def list_tracking_events(
    shipping_order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shipping_order = get_shipping_order_or_404(
        db, shipping_order_id
    )
    ensure_access(db, shipping_order, current_user)

    return (
        db.query(ShipmentTracking)
        .filter(
            ShipmentTracking.shipping_order_id == shipping_order_id
        )
        .order_by(
            ShipmentTracking.event_time.desc(),
            ShipmentTracking.id.desc(),
        )
        .all()
    )


@router.get(
    "/{shipping_order_id}/latest",
    response_model=ShipmentTrackingResponse | None,
)
def get_latest_tracking_event(
    shipping_order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shipping_order = get_shipping_order_or_404(
        db, shipping_order_id
    )
    ensure_access(db, shipping_order, current_user)

    return (
        db.query(ShipmentTracking)
        .filter(
            ShipmentTracking.shipping_order_id == shipping_order_id
        )
        .order_by(
            ShipmentTracking.event_time.desc(),
            ShipmentTracking.id.desc(),
        )
        .first()
    )
