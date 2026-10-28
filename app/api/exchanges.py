
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.shipping_order import ShippingOrder
from app.models.exchange_request import ExchangeRequest
from app.models.user import User, UserRole
from app.schemas.exchanges import (
    ExchangeDecisionUpdate,
    ExchangeRequestCreate,
    ExchangeResponse,
    ExchangeStatusUpdate,
)

router = APIRouter(prefix="/exchanges", tags=["Exchange Management"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.SUPPORT_AGENT,
}

PROCESSING_ROLES = STAFF_ROLES | {
    UserRole.DELIVERY_MANAGER,
}

DECISION_STATUSES = {"APPROVED", "REJECTED"}

NON_COUNTING_STATUSES = {"REJECTED", "CANCELLED"}

EXCHANGE_TRANSITIONS = {
    "APPROVED": {"ITEM_RECEIVED"},
    "ITEM_RECEIVED": {"REPLACEMENT_DISPATCHED"},
    "REPLACEMENT_DISPATCHED": {"COMPLETED"},
}


def role_value(user: User) -> str:
    return str(getattr(user.role, "value", user.role)).upper()


def is_exchange_staff(user: User) -> bool:
    return role_value(user) in {
        role.value for role in STAFF_ROLES
    }


def get_exchange_or_404(
    db: Session, exchange_id: int
) -> ExchangeRequest:
    exchange = (
        db.query(ExchangeRequest)
        .filter(ExchangeRequest.id == exchange_id)
        .first()
    )

    if not exchange:
        raise HTTPException(
            status_code=404,
            detail="Exchange request not found",
        )

    return exchange


def ensure_exchange_access(
    exchange: ExchangeRequest,
    user: User,
) -> None:
    if is_exchange_staff(user) or exchange.user_id == user.id:
        return

    raise HTTPException(status_code=403, detail="Access denied")


def order_is_delivered(db: Session, order: Order) -> bool:
    if order.status.upper() == "DELIVERED":
        return True

    shipment = (
        db.query(ShippingOrder.id)
        .filter(
            ShippingOrder.order_id == order.id,
            ShippingOrder.status == "DELIVERED",
        )
        .first()
    )

    return shipment is not None


@router.post("", response_model=ExchangeResponse, status_code=201)
def request_exchange(
    payload: ExchangeRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(Order)
        .filter(Order.id == payload.order_id)
        .with_for_update()
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only request exchanges for your own orders",
        )

    if not order_is_delivered(db, order):
        raise HTTPException(
            status_code=400,
            detail="Exchanges can only be requested for delivered orders",
        )

    item = (
        db.query(OrderItem)
        .filter(
            OrderItem.id == payload.order_item_id,
            OrderItem.order_id == order.id,
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Order item not found in this order",
        )

    replacement_product = (
        db.query(Product)
        .filter(Product.id == payload.replacement_product_id)
        .first()
    )

    if not replacement_product:
        raise HTTPException(
            status_code=404,
            detail="Replacement product not found",
        )

    existing_exchanges = (
        db.query(ExchangeRequest)
        .filter(
            ExchangeRequest.order_item_id == item.id,
            ~ExchangeRequest.status.in_(NON_COUNTING_STATUSES),
        )
        .all()
    )

    previously_requested = sum(
        exchange.quantity for exchange in existing_exchanges
    )

    if previously_requested + payload.quantity > item.quantity:
        raise HTTPException(
            status_code=400,
            detail="Exchange quantity exceeds the remaining eligible quantity",
        )

    exchange = ExchangeRequest(
        order_id=order.id,
        order_item_id=item.id,
        user_id=current_user.id,
        replacement_product_id=payload.replacement_product_id,
        quantity=payload.quantity,
        reason=payload.reason.strip(),
        status="REQUESTED",
    )

    db.add(exchange)
    db.commit()
    db.refresh(exchange)

    return exchange


@router.get("", response_model=list[ExchangeResponse])
def list_exchanges(
    status: str | None = Query(default=None, max_length=40),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ExchangeRequest)

    if not is_exchange_staff(current_user):
        query = query.filter(
            ExchangeRequest.user_id == current_user.id
        )

    if status:
        query = query.filter(
            ExchangeRequest.status == status.strip().upper()
        )

    return (
        query.order_by(ExchangeRequest.requested_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{exchange_id}", response_model=ExchangeResponse)
def get_exchange(
    exchange_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exchange = get_exchange_or_404(db, exchange_id)
    ensure_exchange_access(exchange, current_user)
    return exchange


@router.patch(
    "/{exchange_id}/decision",
    response_model=ExchangeResponse,
)
def decide_exchange(
    exchange_id: int,
    payload: ExchangeDecisionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_exchange_staff(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only authorized staff can decide exchange requests",
        )

    exchange = (
        db.query(ExchangeRequest)
        .filter(ExchangeRequest.id == exchange_id)
        .with_for_update()
        .first()
    )

    if not exchange:
        raise HTTPException(
            status_code=404,
            detail="Exchange request not found",
        )

    new_status = payload.status.strip().upper()

    if new_status not in DECISION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Decision must be APPROVED or REJECTED",
        )

    if exchange.status != "REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Only requested exchanges can be approved or rejected",
        )

    exchange.status = new_status
    exchange.decision_note = (
        payload.decision_note.strip()
        if payload.decision_note
        else None
    )
    exchange.processed_by = current_user.id

    db.commit()
    db.refresh(exchange)

    return exchange


@router.patch(
    "/{exchange_id}/status",
    response_model=ExchangeResponse,
)
def update_exchange_status(
    exchange_id: int,
    payload: ExchangeStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if role_value(current_user) not in {
        role.value for role in PROCESSING_ROLES
    }:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to process exchanges",
        )

    exchange = (
        db.query(ExchangeRequest)
        .filter(ExchangeRequest.id == exchange_id)
        .with_for_update()
        .first()
    )

    if not exchange:
        raise HTTPException(
            status_code=404,
            detail="Exchange request not found",
        )

    new_status = payload.status.strip().upper()
    allowed = EXCHANGE_TRANSITIONS.get(exchange.status, set())

    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid exchange status transition: "
                f"{exchange.status} to {new_status}"
            ),
        )

    exchange.status = new_status

    if payload.decision_note is not None:
        exchange.decision_note = payload.decision_note.strip() or None

    exchange.processed_by = current_user.id

    db.commit()
    db.refresh(exchange)

    return exchange


@router.post(
    "/{exchange_id}/cancel",
    response_model=ExchangeResponse,
)
def cancel_exchange(
    exchange_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exchange = (
        db.query(ExchangeRequest)
        .filter(ExchangeRequest.id == exchange_id)
        .with_for_update()
        .first()
    )

    if not exchange:
        raise HTTPException(
            status_code=404,
            detail="Exchange request not found",
        )

    if exchange.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if exchange.status != "REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Only pending exchange requests can be cancelled",
        )

    exchange.status = "CANCELLED"

    db.commit()
    db.refresh(exchange)

    return exchange
