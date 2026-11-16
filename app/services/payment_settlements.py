"""Settlement statements: provider-reported totals vs. our own payment ledger."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.payment_transaction import PaymentTransaction
from app.services.payments import SUCCESS_EVENTS
from app.services.pricing import money


def _sum(db: Session, *filters) -> Decimal:
    value = db.query(func.coalesce(func.sum(PaymentTransaction.amount), 0)).filter(*filters).scalar()
    return money(Decimal(str(value or 0)))


def expected_totals(db: Session, currency: str, start: datetime, end: datetime) -> tuple[Decimal, Decimal]:
    """Money our ledger says was captured / refunded in [start, end)."""
    window = (
        PaymentTransaction.currency == currency.upper(),
        PaymentTransaction.created_at >= start,
        PaymentTransaction.created_at < end,
    )
    gross = _sum(
        db, *window,
        PaymentTransaction.event_type.in_(SUCCESS_EVENTS),
        PaymentTransaction.status.in_(("SUCCEEDED", "DUPLICATE_PAID")),  # duplicates were really captured
    )
    refunds = _sum(
        db, *window,
        PaymentTransaction.event_type == "REFUND_SUCCEEDED",
        PaymentTransaction.status == "SUCCEEDED",
    )
    return gross, refunds
