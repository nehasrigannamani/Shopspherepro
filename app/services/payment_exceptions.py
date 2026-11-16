"""Finance exception queue: anomalies that need a human decision."""
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.payment_ops import PaymentException
from app.models.user import UserRole
from app.services.notify import notify_roles

EXCEPTION_TYPES = {
    "AMOUNT_MISMATCH", "DUPLICATE_CAPTURE", "LATE_PAYMENT_AFTER_CANCEL",
    "STATUS_DIVERGENCE", "REFUND_FAILED", "SETTLEMENT_VARIANCE",
}


def raise_exception(
    db: Session, exc_type: str, *, payment: Payment | None = None, order_id: int | None = None,
    refund_id: int | None = None, settlement_id: int | None = None, key_suffix: str = "",
    severity: str = "MEDIUM", summary: str, details: dict | None = None, notify: bool = True,
) -> PaymentException:
    """Create (once) an exception. The same anomaly raised again returns the existing row,
    so webhook replays and repeated reconciliations never flood the queue."""
    key = f"{exc_type}:{payment.id if payment else '-'}:{key_suffix}"[:160]
    existing = db.query(PaymentException).filter(PaymentException.dedupe_key == key).first()
    if existing is not None:
        return existing
    row = PaymentException(
        exc_type=exc_type, severity=severity, status="OPEN", dedupe_key=key, summary=summary[:500],
        payment_id=payment.id if payment else None,
        order_id=order_id if order_id is not None else (payment.order_id if payment else None),
        refund_id=refund_id, settlement_id=settlement_id, details=details,
    )
    db.add(row)
    db.flush()
    if notify:
        notify_roles(
            db, (UserRole.FINANCE_MANAGER, UserRole.ADMIN), f"Payment exception: {exc_type}", summary,
            notification_type="PAYMENT", reference_type="payment_exception", reference_id=row.id,
        )
    return row
