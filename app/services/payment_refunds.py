"""Refund requests (v2) and controlled retry of approved refunds."""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.user import User, UserRole
from app.services.notify import notify_roles
from app.services.payments import PAID_STATUSES, RefundError, committed_refund_total, process_refund


def create_refund_request(db: Session, payment_id: int, amount: Decimal, reason: str,
                          user: User, is_staff: bool) -> Refund:
    """Add a refund request. Caller commits. The payment row is locked so two requests
    can never both fit into the same remaining balance."""
    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    order = db.get(Order, payment.order_id) if payment else None
    if payment is None or order is None or (not is_staff and order.user_id != user.id):
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in PAID_STATUSES:
        raise HTTPException(status_code=409, detail="Only successfully paid payments can be refunded")
    if str(order.status).upper() == "CANCELLED":
        raise HTTPException(status_code=409, detail="This order is cancelled; its refund is handled automatically")
    remaining = Decimal(str(payment.amount)) - committed_refund_total(db, payment.id)
    if amount > remaining:
        raise HTTPException(status_code=409, detail=f"Refund exceeds the remaining refundable amount: {remaining}")
    refund = Refund(
        payment_id=payment.id, order_id=order.id, requested_by=user.id, amount=amount,
        currency=payment.currency, reason=reason.strip(), status="REQUESTED",
    )
    db.add(refund)
    db.flush()
    notify_roles(
        db, (UserRole.FINANCE_MANAGER,), "Refund requested",
        f"Refund of {refund.amount} {refund.currency} requested for order {order.order_number}.",
        notification_type="REFUND", reference_type="refund", reference_id=refund.id,
    )
    return refund


def process_approved_refunds(db: Session, actor_id: int | None, limit: int = 10) -> dict:
    """Send APPROVED refunds to the gateway. Each is committed on its own, so one gateway
    failure leaves that refund FAILED/APPROVED for the next pass and never blocks the rest."""
    ids = list(db.scalars(
        select(Refund.id).where(Refund.status == "APPROVED").order_by(Refund.id).limit(limit)
    ).all())
    summary = {"attempted": len(ids), "succeeded": 0, "failed": 0, "skipped": 0}
    for rid in ids:
        try:
            refund = process_refund(db, rid, actor_id=actor_id)
            if refund.status == "SUCCEEDED":
                summary["succeeded"] += 1
            elif refund.status == "FAILED":
                summary["failed"] += 1
        except RefundError:
            db.rollback()
            summary["skipped"] += 1      # e.g. cash payment: needs manual settlement
        except Exception:                # gateway outage etc.: stays APPROVED, retried next pass
            db.rollback()
            summary["failed"] += 1
    return summary
