
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.return_request import ReturnRequest
from app.models.shipping_order import ShippingOrder
from app.models.user import User, UserRole
from app.schemas.returns import (
    ReturnDecisionUpdate,
    ReturnRequestCreate,
    ReturnResponse,
    ReturnStatusUpdate,
)

router = APIRouter(prefix="/returns", tags=["Return Management"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.SUPPORT_AGENT,
}

DELIVERY_ROLES = STAFF_ROLES | {UserRole.DELIVERY_MANAGER}

NON_COUNTING_STATUSES = {"REJECTED", "CANCELLED"}
DECISION_STATUSES = {"APPROVED", "REJECTED"}

RETURN_TRANSITIONS = {
    "APPROVED": {"RECEIVED"},
    "RECEIVED": {"COMPLETED"},
}


def role_value(user: User) -> str:
    return str(getattr(user.role, "value", user.role)).upper()


def is_staff(user: User) -> bool:
    return role_value(user) in {
        role.value for role in STAFF_ROLES
    }


def get_return_or_404(
    db: Session, return_id: int
) -> ReturnRequest:
    record = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=404, detail="Return request not found"
        )
    return record


def ensure_return_access(
    record: ReturnRequest, user: User
) -> None:
    if is_staff(user) or record.user_id == user.id:
        return

    raise HTTPException(status_code=403, detail="Access denied")


def order_is_delivered(db: Session, order: Order) -> bool:
    if order.status.upper() == "DELIVERED":
        return True

    # The delivery module tracks shipment delivery separately from
    # the parent order's status, so accept either delivered record.
    delivered_shipment = (
        db.query(ShippingOrder.id)
        .filter(
            ShippingOrder.order_id == order.id,
            ShippingOrder.status == "DELIVERED",
        )
        .first()
    )
    return delivered_shipment is not None


@router.post("", response_model=ReturnResponse, status_code=201)
def request_return(
    payload: ReturnRequestCreate,
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
            detail="You can only request returns for your own orders",
        )

    if not order_is_delivered(db, order):
        raise HTTPException(
            status_code=400,
            detail="Returns can only be requested for delivered orders",
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

    counted_records = (
        db.query(ReturnRequest)
        .filter(
            ReturnRequest.order_item_id == item.id,
            ~ReturnRequest.status.in_(NON_COUNTING_STATUSES),
        )
        .all()
    )
    previously_requested = sum(
        record.quantity for record in counted_records
    )

    if previously_requested + payload.quantity > item.quantity:
        raise HTTPException(
            status_code=400,
            detail="Requested return quantity exceeds the remaining quantity",
        )

    record = ReturnRequest(
        order_id=order.id,
        order_item_id=item.id,
        user_id=current_user.id,
        quantity=payload.quantity,
        reason=payload.reason.strip(),
        status="REQUESTED",
    )

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("", response_model=list[ReturnResponse])
def list_returns(
    status: str | None = Query(default=None, max_length=30),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ReturnRequest)

    if not is_staff(current_user):
        query = query.filter(
            ReturnRequest.user_id == current_user.id
        )

    if status:
        query = query.filter(
            ReturnRequest.status == status.strip().upper()
        )

    return (
        query.order_by(ReturnRequest.requested_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{return_id}", response_model=ReturnResponse)
def get_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = get_return_or_404(db, return_id)
    ensure_return_access(record, current_user)
    return record


@router.patch(
    "/{return_id}/decision",
    response_model=ReturnResponse,
)
def decide_return(
    return_id: int,
    payload: ReturnDecisionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_staff(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only authorized staff can decide return requests",
        )

    record = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .with_for_update()
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=404, detail="Return request not found"
        )

    new_status = payload.status.strip().upper()
    if new_status not in DECISION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Decision must be APPROVED or REJECTED",
        )

    if record.status != "REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Only requested returns can be approved or rejected",
        )

    record.status = new_status
    record.decision_note = (
        payload.decision_note.strip()
        if payload.decision_note
        else None
    )
    record.processed_by = current_user.id

    db.commit()
    db.refresh(record)
    return record


@router.patch(
    "/{return_id}/status",
    response_model=ReturnResponse,
)
def update_return_status(
    return_id: int,
    payload: ReturnStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if role_value(current_user) not in {
        role.value for role in DELIVERY_ROLES
    }:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update return processing",
        )

    record = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .with_for_update()
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=404, detail="Return request not found"
        )

    new_status = payload.status.strip().upper()
    allowed = RETURN_TRANSITIONS.get(record.status, set())

    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid return status transition from {record.status} to {new_status}",
        )

    record.status = new_status
    if payload.decision_note is not None:
        record.decision_note = payload.decision_note.strip() or None

    record.processed_by = current_user.id

    db.commit()
    db.refresh(record)
    return record


@router.post(
    "/{return_id}/cancel",
    response_model=ReturnResponse,
)
def cancel_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .with_for_update()
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=404, detail="Return request not found"
        )

    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if record.status != "REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Only pending return requests can be cancelled",
        )

    record.status = "CANCELLED"
    db.commit()
    db.refresh(record)
    return record
