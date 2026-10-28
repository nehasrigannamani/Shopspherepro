
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.shipping_order import ShippingOrder
from app.models.user import User, UserRole
from app.schemas.shipping_orders import (
    ShippingOrderCreate,
    ShippingOrderResponse,
    ShippingOrderUpdate,
    ShippingStatusUpdate,
)

router = APIRouter(prefix="/shipping/orders", tags=["Shipping Orders"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.DELIVERY_MANAGER,
}

ALLOWED_TRANSITIONS = {
    "PREPARING": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"IN_TRANSIT", "DELIVERED", "DELIVERY_FAILED"},
    "IN_TRANSIT": {"DELIVERED", "DELIVERY_FAILED"},
    "DELIVERY_FAILED": {"IN_TRANSIT", "DELIVERED", "RETURNED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
    "RETURNED": set(),
}


def role_value(user: User) -> str:
    return str(getattr(user.role, "value", user.role)).upper()


def require_shipping_staff(user: User) -> None:
    if user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to manage shipping orders",
        )


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
            status_code=404, detail="Shipping order not found"
        )
    return shipping_order


def ensure_order_access(
    db: Session, shipping_order: ShippingOrder, user: User
) -> None:
    if role_value(user) in {role.value for role in STAFF_ROLES}:
        return

    order = db.query(Order).filter(Order.id == shipping_order.order_id).first()
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.post(
    "/{order_id}",
    response_model=ShippingOrderResponse,
    status_code=201,
)
def create_shipping_order(
    order_id: int,
    payload: ShippingOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_shipping_staff(current_user)

    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in {"CONFIRMED", "PROCESSING"}:
        raise HTTPException(
            status_code=400,
            detail="Shipping can only be created for confirmed or processing orders",
        )

    existing = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.order_id == order_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A shipping record already exists for this order",
        )

    shipping_order = ShippingOrder(
        order_id=order_id,
        carrier_name=payload.carrier_name.strip(),
        tracking_number=(
            payload.tracking_number.strip()
            if payload.tracking_number
            else None
        ),
        estimated_delivery_at=payload.estimated_delivery_at,
        notes=payload.notes,
        created_by=current_user.id,
        status="PREPARING",
    )

    try:
        db.add(shipping_order)
        db.commit()
        db.refresh(shipping_order)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A shipping record or tracking number already exists",
        )

    return shipping_order


@router.get("", response_model=list[ShippingOrderResponse])
def list_shipping_orders(
    status: str | None = Query(default=None, max_length=30),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ShippingOrder)

    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        query = query.join(
            Order, ShippingOrder.order_id == Order.id
        ).filter(Order.user_id == current_user.id)

    if status:
        query = query.filter(
            ShippingOrder.status == status.strip().upper()
        )

    return (
        query.order_by(ShippingOrder.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{shipping_order_id}", response_model=ShippingOrderResponse)
def get_shipping_order(
    shipping_order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shipping_order = get_shipping_order_or_404(db, shipping_order_id)
    ensure_order_access(db, shipping_order, current_user)
    return shipping_order


@router.patch(
    "/{shipping_order_id}/details",
    response_model=ShippingOrderResponse,
)
def update_shipping_details(
    shipping_order_id: int,
    payload: ShippingOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_shipping_staff(current_user)

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == shipping_order_id)
        .with_for_update()
        .first()
    )
    if not shipping_order:
        raise HTTPException(
            status_code=404, detail="Shipping order not found"
        )

    if shipping_order.status in {"DELIVERED", "CANCELLED", "RETURNED"}:
        raise HTTPException(
            status_code=400,
            detail="Shipping details cannot be changed after a terminal status",
        )

    updates = payload.model_dump(exclude_unset=True)

    if "carrier_name" in updates and updates["carrier_name"] is not None:
        updates["carrier_name"] = updates["carrier_name"].strip()

    if "tracking_number" in updates:
        value = updates["tracking_number"]
        updates["tracking_number"] = value.strip() if value else None

    for field, value in updates.items():
        setattr(shipping_order, field, value)

    try:
        db.commit()
        db.refresh(shipping_order)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="That tracking number is already assigned to another shipment",
        )

    return shipping_order


@router.patch(
    "/{shipping_order_id}/status",
    response_model=ShippingOrderResponse,
)
def update_shipping_status(
    shipping_order_id: int,
    payload: ShippingStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_shipping_staff(current_user)

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.id == shipping_order_id)
        .with_for_update()
        .first()
    )
    if not shipping_order:
        raise HTTPException(
            status_code=404, detail="Shipping order not found"
        )

    new_status = payload.status.strip().upper()
    old_status = shipping_order.status.upper()

    if new_status not in ALLOWED_TRANSITIONS:
        raise HTTPException(
            status_code=400, detail="Unsupported shipping status"
        )

    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid shipping status transition: {old_status} to {new_status}",
        )

    now = datetime.now(timezone.utc)

    if new_status == "SHIPPED":
        if not shipping_order.tracking_number:
            raise HTTPException(
                status_code=400,
                detail="Add a tracking number before marking the shipment as shipped",
            )
        shipping_order.shipped_at = now

    if new_status == "DELIVERED":
        shipping_order.delivered_at = now

    shipping_order.status = new_status

    if payload.notes is not None:
        shipping_order.notes = payload.notes

    try:
        db.commit()
        db.refresh(shipping_order)
    except Exception:
        db.rollback()
        raise

    return shipping_order
