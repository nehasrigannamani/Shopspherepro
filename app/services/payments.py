"""Payment state machine: webhook events, gateway sync and refunds.

Every state change is idempotent:
* webhook events are de-duplicated by ``PaymentTransaction.provider_event_id``
  (unique) and by locking the payment row;
* refunds use a stable idempotency key per refund record.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.order_status_history import OrderStatusHistory
from app.models.payment import Payment
from app.models.payment_transaction import PaymentTransaction
from app.models.refund import Refund
from app.models.user import UserRole
from app.services import stripe_gateway
from app.services.audit import write_audit_log
from app.services.notify import create_notification, notify_roles

logger = logging.getLogger("shopsphere.payments")

SUCCESS_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}
FAILURE_EVENTS = {
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
}
REFUND_EVENTS = {"refund.updated", "charge.refund.updated"}
PAID_STATUSES = {"SUCCEEDED", "PAID", "SUCCESS", "COMPLETED"}
ACTIVE_REFUND_STATUSES = ("REQUESTED", "APPROVED", "PROCESSING", "SUCCEEDED")


def _event_seen(db: Session, event_id: str) -> bool:
    return (
        db.query(PaymentTransaction.id)
        .filter(PaymentTransaction.provider_event_id == event_id)
        .first()
        is not None
    )


def _record(
    db: Session,
    payment: Payment,
    *,
    event_type: str,
    status: str,
    event_id: str | None,
    message: str,
    payload: dict | None = None,
    amount: Decimal | None = None,
) -> None:
    db.add(
        PaymentTransaction(
            payment_id=payment.id,
            event_type=event_type,
            provider_event_id=event_id,
            status=status,
            amount=amount if amount is not None else payment.amount,
            currency=payment.currency,
            message=message,
            raw_payload=payload,
        )
    )


def _safe_payload(event: dict, session: dict) -> dict:
    """Store only non-sensitive identifiers from the gateway payload."""
    return {
        "event_id": event.get("id"),
        "type": event.get("type"),
        "session_id": session.get("id"),
        "payment_status": session.get("payment_status"),
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
    }


def refunded_total(db: Session, payment_id: int) -> Decimal:
    value = (
        db.query(func.coalesce(func.sum(Refund.amount), 0))
        .filter(Refund.payment_id == payment_id, Refund.status == "SUCCEEDED")
        .scalar()
    )
    return Decimal(str(value or 0))


def committed_refund_total(db: Session, payment_id: int) -> Decimal:
    value = (
        db.query(func.coalesce(func.sum(Refund.amount), 0))
        .filter(
            Refund.payment_id == payment_id,
            Refund.status.in_(ACTIVE_REFUND_STATUSES),
        )
        .scalar()
    )
    return Decimal(str(value or 0))


def apply_payment_success(
    db: Session,
    payment: Payment,
    session: dict,
    *,
    event_type: str,
    event_id: str,
    event: dict,
) -> str:
    """Mark a payment paid. Caller holds the payment row lock."""
    order = (
        db.query(Order).filter(Order.id == payment.order_id).with_for_update().first()
    )

    expected = stripe_gateway.to_minor_units(payment.amount)
    if (
        session.get("amount_total") is not None
        and int(session["amount_total"]) != expected
    ) or (
        session.get("currency")
        and str(session["currency"]).upper() != payment.currency.upper()
    ):
        payment.status = "AMOUNT_MISMATCH"
        _record(
            db, payment, event_type=event_type, status="AMOUNT_MISMATCH",
            event_id=event_id,
            message="Gateway amount/currency differs from the order",
            payload=_safe_payload(event, session),
        )
        notify_roles(
            db,
            (UserRole.FINANCE_MANAGER, UserRole.ADMIN),
            "Payment amount mismatch",
            f"Payment {payment.id} for order {payment.order_id} did not "
            "match the gateway amount and was not applied.",
            notification_type="PAYMENT",
            reference_type="payment",
            reference_id=payment.id,
        )
        return "amount_mismatch"

    if session.get("payment_status") not in (None, "paid", "no_payment_required"):
        _record(
            db, payment, event_type=event_type, status="PENDING",
            event_id=event_id,
            message="Checkout completed; payment still awaiting funds",
            payload=_safe_payload(event, session),
        )
        return "awaiting_funds"

    if payment.status in PAID_STATUSES:
        _record(
            db, payment, event_type=event_type, status="DUPLICATE_IGNORED",
            event_id=event_id,
            message="Payment already recorded as successful",
            payload=_safe_payload(event, session),
        )
        return "already_paid"

    payment.status = "SUCCEEDED"
    intent = session.get("payment_intent")
    if intent:
        payment.provider_payment_intent = str(intent)

    order.payment_status = "PAID"
    order.payment_method = order.payment_method or "CARD"

    previous = str(order.status).upper()
    if previous in {"PLACED", "PENDING"}:
        order.status = "CONFIRMED"
        db.add(
            OrderStatusHistory(
                order_id=order.id,
                previous_status=previous,
                new_status="CONFIRMED",
                changed_by=None,
                note="System: payment received",
            )
        )
        create_notification(
            db,
            order.user_id,
            f"Order {order.order_number} confirmed",
            "We received your payment and your order is confirmed.",
            notification_type="ORDER",
            reference_type="order",
            reference_id=order.id,
            email=True,
            email_subject=f"Payment received for {order.order_number}",
        )
    elif previous == "CANCELLED":
        # Money arrived for an order that was cancelled meanwhile: refund it.
        db.add(
            Refund(
                payment_id=payment.id,
                order_id=order.id,
                requested_by=order.user_id,
                amount=payment.amount,
                currency=payment.currency,
                reason="Payment received after order cancellation",
                status="APPROVED",
                decision_note="Auto-approved by system",
            )
        )
        notify_roles(
            db,
            (UserRole.FINANCE_MANAGER,),
            "Refund needed for cancelled order",
            f"Order {order.order_number} was paid after cancellation; a "
            "refund was queued for processing.",
            notification_type="REFUND",
            reference_type="order",
            reference_id=order.id,
        )

    create_notification(
        db,
        order.user_id,
        "Payment successful",
        f"Payment of {payment.amount} {payment.currency} for order "
        f"{order.order_number} succeeded.",
        notification_type="PAYMENT",
        reference_type="payment",
        reference_id=payment.id,
        email=True,
    )

    _record(
        db, payment, event_type=event_type, status="SUCCEEDED",
        event_id=event_id, message="Payment confirmed by gateway",
        payload=_safe_payload(event, session),
    )
    write_audit_log(
        db, actor_user_id=None, action="PAYMENT_SUCCEEDED",
        entity_type="payment", entity_id=payment.id,
        details={"order_id": order.id, "event_id": event_id}, commit=False,
    )
    return "payment_succeeded"


def apply_payment_failure(
    db: Session,
    payment: Payment,
    session: dict,
    *,
    event_type: str,
    event_id: str,
    event: dict,
) -> str:
    if payment.status in PAID_STATUSES:
        _record(
            db, payment, event_type=event_type, status="DUPLICATE_IGNORED",
            event_id=event_id,
            message="Failure event ignored: payment already succeeded",
            payload=_safe_payload(event, session),
        )
        return "ignored_after_success"

    new_status = "EXPIRED" if event_type.endswith("expired") else "FAILED"
    payment.status = new_status

    order = db.query(Order).filter(Order.id == payment.order_id).first()
    if order is not None and order.payment_status != "PAID":
        order.payment_status = "FAILED"
        create_notification(
            db,
            order.user_id,
            "Payment not completed",
            f"Payment for order {order.order_number} was not completed. "
            "You can try paying again.",
            notification_type="PAYMENT",
            reference_type="payment",
            reference_id=payment.id,
            email=True,
        )
        notify_roles(
            db,
            (UserRole.ORDER_MANAGER,),
            "Payment failed for an order",
            f"Order {order.order_number} payment {new_status.lower()}.",
            notification_type="PAYMENT",
            reference_type="order",
            reference_id=order.id,
        )

    _record(
        db, payment, event_type=event_type, status=new_status,
        event_id=event_id, message=f"Gateway reported {new_status.lower()}",
        payload=_safe_payload(event, session),
    )
    return "payment_failed"


def settle_refund_totals(db: Session, payment: Payment) -> None:
    """Update payment/order payment state after a refund succeeded."""
    # The session runs with autoflush disabled, so write the refund status
    # first; otherwise the SUM below would not see it.
    db.flush()
    total = refunded_total(db, payment.id)
    order = db.query(Order).filter(Order.id == payment.order_id).first()
    if total >= Decimal(str(payment.amount)):
        payment.status = "REFUNDED"
        if order is not None:
            order.payment_status = "REFUNDED"
    elif total > 0 and order is not None:
        order.payment_status = "PARTIALLY_REFUNDED"


def _finish_refund(
    db: Session, refund: Refund, *, succeeded: bool, reason: str | None = None
) -> None:
    payment = db.query(Payment).filter(Payment.id == refund.payment_id).first()
    if succeeded:
        refund.status = "SUCCEEDED"
        refund.processed_at = datetime.now(timezone.utc)
        refund.failure_reason = None
        settle_refund_totals(db, payment)
    else:
        refund.status = "FAILED"
        refund.failure_reason = reason

    _record(
        db, payment,
        event_type="REFUND_SUCCEEDED" if succeeded else "REFUND_FAILED",
        status=refund.status, event_id=None,
        message=reason or "Refund completed",
        amount=Decimal(str(refund.amount)),
        payload={"refund_id": refund.id, "provider_reference": refund.provider_reference},
    )
    create_notification(
        db,
        refund.requested_by,
        "Refund update",
        (
            f"Your refund of {refund.amount} {refund.currency} was completed."
            if succeeded
            else "Your refund could not be completed yet; our team is reviewing it."
        ),
        notification_type="REFUND",
        reference_type="refund",
        reference_id=refund.id,
        email=True,
    )


class RefundError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def process_refund(
    db: Session,
    refund_id: int,
    *,
    actor_id: int | None,
    manual_settlement: bool = False,
) -> Refund:
    """Send an APPROVED (or previously FAILED) refund to the gateway.

    The row is moved to PROCESSING and committed *before* the gateway call so
    concurrent requests cannot both call the gateway; the stable idempotency
    key protects against retries after a crash.
    """
    refund = (
        db.query(Refund).filter(Refund.id == refund_id).with_for_update().first()
    )
    if refund is None:
        raise RefundError(404, "Refund not found")
    if refund.status not in {"APPROVED", "FAILED"}:
        raise RefundError(
            409, f"Refund in status {refund.status} cannot be processed"
        )

    payment = (
        db.query(Payment).filter(Payment.id == refund.payment_id).with_for_update().first()
    )
    if payment is None or payment.status not in PAID_STATUSES | {"REFUNDED"}:
        raise RefundError(409, "The original payment is not refundable")

    # An APPROVED refund is already counted in the committed total; a FAILED
    # one is not, so add it back before comparing with the payment amount.
    projected = committed_refund_total(db, payment.id)
    if refund.status == "FAILED":
        projected += Decimal(str(refund.amount))
    if projected > Decimal(str(payment.amount)):
        raise RefundError(409, "Refund exceeds the refundable amount")

    refund.processed_by = actor_id or refund.processed_by

    # Payments without a gateway intent (e.g. cash on delivery) are settled
    # manually by finance staff.
    if not payment.provider_payment_intent:
        if not manual_settlement:
            raise RefundError(
                409,
                "No gateway payment exists for this order; settle it "
                "manually and retry with manual_settlement=true",
            )
        refund.provider_reference = f"MANUAL-{refund.id}"
        _finish_refund(db, refund, succeeded=True)
        db.commit()
        db.refresh(refund)
        return refund

    refund.status = "PROCESSING"
    db.commit()

    try:
        result = stripe_gateway.create_refund(
            payment_intent_id=payment.provider_payment_intent,
            amount=Decimal(str(refund.amount)),
            idempotency_key=f"refund-{refund.id}",
            refund_id=refund.id,
        )
    except stripe_gateway.GatewayError as exc:
        refund = db.query(Refund).filter(Refund.id == refund_id).with_for_update().first()
        _finish_refund(db, refund, succeeded=False, reason=str(exc))
        db.commit()
        db.refresh(refund)
        return refund

    refund = db.query(Refund).filter(Refund.id == refund_id).with_for_update().first()
    refund.provider_reference = result["id"]
    gateway_status = result.get("status")
    if gateway_status == "succeeded":
        _finish_refund(db, refund, succeeded=True)
    elif gateway_status in {"failed", "canceled"}:
        _finish_refund(db, refund, succeeded=False, reason=f"Gateway: {gateway_status}")
    # "pending" stays PROCESSING until refund.updated arrives.

    write_audit_log(
        db, actor_user_id=actor_id, action="REFUND_PROCESSED",
        entity_type="refund", entity_id=refund.id,
        details={"status": refund.status, "amount": str(refund.amount)},
        commit=False,
    )
    db.commit()
    db.refresh(refund)
    return refund


def handle_stripe_event(db: Session, event: dict) -> str:
    """Apply a verified Stripe event. Returns a short outcome string."""
    event_id = event.get("id")
    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if not event_id:
        return "ignored"
    if _event_seen(db, event_id):
        return "duplicate"

    if event_type in REFUND_EVENTS:
        return _handle_refund_event(db, event_id, event_type, obj)

    if event_type not in SUCCESS_EVENTS | FAILURE_EVENTS:
        return "ignored"

    payment = (
        db.query(Payment)
        .filter(Payment.provider_reference == obj.get("id"))
        .with_for_update()
        .first()
    )
    if payment is None:
        return "unknown_payment"

    # Re-check after taking the lock: a concurrent delivery may have won.
    if _event_seen(db, event_id):
        db.rollback()
        return "duplicate"

    try:
        if event_type in SUCCESS_EVENTS:
            outcome = apply_payment_success(
                db, payment, obj, event_type=event_type,
                event_id=event_id, event=event,
            )
        else:
            outcome = apply_payment_failure(
                db, payment, obj, event_type=event_type,
                event_id=event_id, event=event,
            )
        db.commit()
        return outcome
    except IntegrityError:
        db.rollback()
        return "duplicate"


def _handle_refund_event(db, event_id, event_type, obj) -> str:
    refund = (
        db.query(Refund)
        .filter(Refund.provider_reference == obj.get("id"))
        .with_for_update()
        .first()
    )
    if refund is None:
        return "unknown_refund"
    if refund.status in {"SUCCEEDED"}:
        return "already_final"

    gateway_status = obj.get("status")
    try:
        if gateway_status == "succeeded":
            _finish_refund(db, refund, succeeded=True)
        elif gateway_status in {"failed", "canceled"}:
            _finish_refund(
                db, refund, succeeded=False,
                reason=obj.get("failure_reason") or f"Gateway: {gateway_status}",
            )
        else:
            return "no_change"
        # Marker row so the same event id is never applied twice.
        payment = db.query(Payment).filter(Payment.id == refund.payment_id).first()
        _record(
            db, payment, event_type=event_type, status=refund.status,
            event_id=event_id, message="Refund status synced from gateway",
            amount=Decimal(str(refund.amount)),
            payload={"refund_id": refund.id},
        )
        db.commit()
        return "refund_updated"
    except IntegrityError:
        db.rollback()
        return "duplicate"
