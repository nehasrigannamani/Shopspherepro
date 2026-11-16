"""Compare local payment state with the provider and repair what is safe to repair.

Safe: apply a paid/expired result through the same idempotent webhook path, and
time out an abandoned PENDING payment. Never automatic: anything that would
change money already recorded as received (that becomes a STATUS_DIVERGENCE exception).
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment import Payment
from app.models.payment_ops import PaymentReconciliation
from app.services import stripe_gateway
from app.services.payment_exceptions import raise_exception
from app.services.payments import PAID_STATUSES, SETTLED_STATUSES, handle_stripe_event
from app.services.reservations import now_naive

OPEN_LOCAL = {"PENDING", "TIMED_OUT"}


def provider_state(session: dict) -> str:
    if session.get("payment_status") == "paid":
        return "PAID"
    if session.get("status") == "expired":
        return "EXPIRED"
    return "OPEN"


def _event(ref: str, suffix: str, event_type: str, session: dict) -> dict:
    return {"id": f"reconcile:{ref}:{suffix}", "type": event_type, "data": {"object": session}}


def _save(db, payment, before, provider, outcome, detail, actor_id) -> PaymentReconciliation:
    db.refresh(payment)
    row = PaymentReconciliation(
        payment_id=payment.id, provider_status=provider, local_status_before=before,
        local_status_after=payment.status, outcome=outcome, detail=detail, triggered_by=actor_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def reconcile_payment(db: Session, payment: Payment, actor_id: int | None,
                      timeout_minutes: int | None = None) -> PaymentReconciliation:
    before = payment.status
    ref = payment.provider_reference
    if payment.provider != "STRIPE" or not ref:
        return _save(db, payment, before, None, "SKIPPED_NO_PROVIDER_REFERENCE",
                     "No gateway session to compare against", actor_id)
    try:
        session = stripe_gateway.retrieve_checkout_session(ref)
    except stripe_gateway.GatewayError:
        return _save(db, payment, before, None, "PROVIDER_UNAVAILABLE",
                     "Gateway could not be reached; nothing changed", actor_id)

    state = provider_state(session)
    local_paid = before in PAID_STATUSES | SETTLED_STATUSES
    outcome, detail = "IN_SYNC", None

    if state == "PAID" and not local_paid:
        if before == "AMOUNT_MISMATCH":
            outcome, detail = "NEEDS_REVIEW", "Provider shows paid but the amount did not match; see exceptions"
        else:
            result = handle_stripe_event(db, _event(ref, "paid", "checkout.session.completed", session))
            outcome, detail = {
                "payment_succeeded": ("APPLIED_PAID", "Provider reported paid; local record updated"),
                "duplicate_capture": ("DUPLICATE_CAPTURE", "Order was already paid; duplicate queued for refund"),
                "amount_mismatch": ("NEEDS_REVIEW", "Amount differs from the order total"),
            }.get(result, ("IN_SYNC", f"Event outcome: {result}"))
    elif state == "EXPIRED" and before in OPEN_LOCAL:
        if before == "PENDING":
            handle_stripe_event(db, _event(ref, "expired", "checkout.session.expired", session))
            outcome, detail = "APPLIED_EXPIRED", "Provider session expired; local record updated"
        else:  # TIMED_OUT locally and now expired at the provider: just settle the label
            payment.status = "EXPIRED"
            outcome, detail = "APPLIED_EXPIRED", "Timed-out session is now expired at the provider"
    elif state == "OPEN" and before == "PENDING":
        limit = timeout_minutes if timeout_minutes is not None else settings.payment_timeout_minutes
        created = payment.created_at.replace(tzinfo=None) if payment.created_at else None
        if created is not None and now_naive() - created >= timedelta(minutes=limit):
            try:  # stop the customer paying a session we have given up on
                stripe_gateway.expire_checkout_session(ref)
            except stripe_gateway.GatewayError:
                pass
            handle_stripe_event(db, _event(ref, "timeout", "payment.timed_out", session))
            outcome, detail = "TIMED_OUT", f"No payment after {limit} minutes"
    elif state in {"OPEN", "EXPIRED"} and local_paid:
        raise_exception(
            db, "STATUS_DIVERGENCE", payment=payment, key_suffix=state, severity="HIGH",
            summary=f"Local payment is {before} but the provider session is {state.lower()}",
            details={"local_status": before, "provider_status": state},
        )
        outcome, detail = "DIVERGENCE", "Local record says paid but provider does not; raised for review"

    return _save(db, payment, before, state, outcome, detail, actor_id)


def reconcile_pending(db: Session, actor_id: int | None, older_than_minutes: int | None = None,
                      limit: int = 25) -> dict:
    minutes = older_than_minutes if older_than_minutes is not None else settings.payment_timeout_minutes
    cutoff = now_naive() - timedelta(minutes=minutes)
    ids = list(db.scalars(
        select(Payment.id)
        .where(Payment.status.in_(OPEN_LOCAL), Payment.created_at <= cutoff, Payment.provider_reference.is_not(None))
        .order_by(Payment.id).limit(limit)
    ).all())
    counts: dict[str, int] = {}
    for pid in ids:
        payment = db.get(Payment, pid)
        row = reconcile_payment(db, payment, actor_id, timeout_minutes=minutes)
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return {"checked": len(ids), "outcomes": counts}
