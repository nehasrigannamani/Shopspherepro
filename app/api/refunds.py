
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.user import User, UserRole
from app.services.notify import create_notification, notify_roles
from app.services.payments import RefundError, process_refund
from app.schemas.refunds import (
    RefundDecisionRequest,
    RefundRequest,
    RefundResponse,
)

router = APIRouter(prefix="/refunds", tags=["Refunds"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.FINANCE_MANAGER,
}

PAID_STATUSES = {"PAID", "SUCCEEDED", "SUCCESS", "COMPLETED"}


def role_value(user: User) -> str:
    role = user.role
    return str(getattr(role, "value", role))


@router.post(
    "",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_refund(
    payload: RefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = (
        db.query(Payment)
        .filter(Payment.id == payload.payment_id)
        .with_for_update()
        .first()
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    order = (
        db.query(Order)
        .filter(Order.id == payment.order_id)
        .first()
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Associated order not found",
        )

    is_staff = role_value(current_user) in {
        role.value for role in STAFF_ROLES
    }

    if not is_staff and order.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot request a refund for this order",
        )

    if str(payment.status).upper() not in PAID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Only successfully paid orders can be refunded",
        )

    if str(order.status).upper() == "CANCELLED":
        raise HTTPException(
            status_code=400,
            detail="This order is cancelled; review its refund status first",
        )

    already_refunded = (
        db.query(func.coalesce(func.sum(Refund.amount), 0))
        .filter(
            Refund.payment_id == payment.id,
            Refund.status.in_(
                ["REQUESTED", "APPROVED", "PROCESSING", "SUCCEEDED"]
            ),
        )
        .scalar()
    )

    remaining = Decimal(str(payment.amount)) - Decimal(
        str(already_refunded or 0)
    )

    if payload.amount > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Refund amount exceeds the remaining refundable amount: {remaining}",
        )

    refund = Refund(
        payment_id=payment.id,
        order_id=order.id,
        requested_by=current_user.id,
        amount=payload.amount,
        currency=payment.currency,
        reason=payload.reason.strip(),
        status="REQUESTED",
    )

    db.add(refund)
    db.flush()
    notify_roles(
        db,
        (UserRole.FINANCE_MANAGER,),
        "Refund requested",
        f"Refund of {refund.amount} {refund.currency} requested for order {order.order_number}.",
        notification_type="REFUND",
        reference_type="refund",
        reference_id=refund.id,
    )

    try:
        db.commit()
        db.refresh(refund)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not create the refund request",
        )

    return refund


@router.get(
    "/{refund_id}",
    response_model=RefundResponse,
)
def get_refund(
    refund_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    refund = db.query(Refund).filter(Refund.id == refund_id).first()

    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")

    is_staff = role_value(current_user) in {
        role.value for role in STAFF_ROLES
    }

    if not is_staff and refund.requested_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot view this refund",
        )

    return refund


@router.get("", response_model=list[RefundResponse])
def list_refunds(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Refund)

    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        query = query.filter(Refund.requested_by == current_user.id)

    return query.order_by(Refund.created_at.desc()).all()


@router.patch(
    "/{refund_id}/decision",
    response_model=RefundResponse,
)
def decide_refund(
    refund_id: int,
    payload: RefundDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        raise HTTPException(
            status_code=403,
            detail="Only authorized finance or admin staff can decide refunds",
        )

    refund = (
        db.query(Refund)
        .filter(Refund.id == refund_id)
        .with_for_update()
        .first()
    )

    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")

    if refund.status != "REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Only requested refunds can be approved or rejected",
        )

    new_status = payload.status.strip().upper()

    if new_status not in {"APPROVED", "REJECTED"}:
        raise HTTPException(
            status_code=422,
            detail="Status must be APPROVED or REJECTED",
        )

    refund.status = new_status
    refund.processed_by = current_user.id
    refund.decision_note = (
        payload.decision_note.strip()
        if payload.decision_note
        else None
    )

    create_notification(
        db,
        refund.requested_by,
        f"Refund {new_status.lower()}",
        f"Your refund request of {refund.amount} {refund.currency} was "
        f"{new_status.lower()}.",
        notification_type="REFUND",
        reference_type="refund",
        reference_id=refund.id,
        email=True,
    )

    try:
        db.commit()
        db.refresh(refund)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not update the refund decision",
        )

    return refund


class RefundProcessRequest(BaseModel):
    manual_settlement: bool = False


@router.post("/{refund_id}/process", response_model=RefundResponse)
def process_refund_endpoint(
    refund_id: int,
    payload: RefundProcessRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send an APPROVED refund to the payment gateway (idempotent)."""
    if role_value(current_user) not in {role.value for role in STAFF_ROLES}:
        raise HTTPException(
            status_code=403,
            detail="Only authorized finance or admin staff can process refunds",
        )
    try:
        return process_refund(
            db,
            refund_id,
            actor_id=current_user.id,
            manual_settlement=bool(payload and payload.manual_settlement),
        )
    except RefundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
