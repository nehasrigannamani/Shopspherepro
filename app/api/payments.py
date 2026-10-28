from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.order import Order
from app.models.payment import Payment
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User, UserRole
from app.schemas.payments import PaymentResponse
from app.services import stripe_gateway
from app.services.payments import PAID_STATUSES, handle_stripe_event

router = APIRouter(prefix="/payments", tags=["Payments"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.FINANCE_MANAGER,
}


async def raw_body(request: Request) -> bytes:
    """The exact bytes Stripe signed; never re-serialise them."""
    return await request.body()


@router.post(
    "/orders/{order_id}/checkout-session",
    response_model=PaymentResponse,
    status_code=201,
)
def create_checkout_session(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if (
        current_user.role != UserRole.CUSTOMER
        and current_user.role not in STAFF_ROLES
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()

    if order is None or (
        order.user_id != current_user.id and current_user.role not in STAFF_ROLES
    ):
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.status).upper() == "CANCELLED":
        raise HTTPException(status_code=409, detail="Cannot pay for a cancelled order")

    if str(order.payment_status).upper() in PAID_STATUSES:
        raise HTTPException(status_code=409, detail="Order is already paid")

    if str(order.payment_method or "").upper() == "COD":
        raise HTTPException(status_code=409, detail="This order is cash on delivery")

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    # Duplicate-safe: the order row is locked, so a repeated call returns the
    # still-open session instead of creating a second payment.
    open_payment = (
        db.query(Payment)
        .filter(Payment.order_id == order.id, Payment.status == "PENDING")
        .order_by(Payment.id.desc())
        .first()
    )
    if open_payment is not None and open_payment.checkout_url:
        return open_payment

    amount = Decimal(str(order.grand_total)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Order total must be greater than zero")

    owner = db.get(User, order.user_id)
    attempt = db.query(Payment).filter(Payment.order_id == order.id).count() + 1

    try:
        session = stripe_gateway.create_checkout_session(
            order_number=order.order_number,
            order_id=order.id,
            user_id=order.user_id,
            customer_email=owner.email,
            amount=amount,
            currency=order.currency or "INR",
            idempotency_key=f"order-{order.id}-attempt-{attempt}",
        )
    except stripe_gateway.GatewayError:
        raise HTTPException(
            status_code=502, detail="The payment gateway is unavailable"
        ) from None

    payment = Payment(
        order_id=order.id,
        user_id=order.user_id,
        provider="STRIPE",
        provider_reference=session["id"],
        checkout_url=session["url"],
        amount=amount,
        currency=(order.currency or "INR").upper(),
        status="PENDING",
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentTransaction(
            payment_id=payment.id,
            event_type="CHECKOUT_SESSION_CREATED",
            provider_event_id=None,
            status="PENDING",
            amount=amount,
            currency=payment.currency,
            message="Stripe Checkout Session created",
            raw_payload={"session_id": session["id"]},
        )
    )
    order.payment_status = "PENDING"
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/webhook")
def stripe_webhook(
    body: bytes = Depends(raw_body),
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    """Stripe → server callback. Signature is verified before anything runs;
    replays of the same event id are acknowledged and ignored."""
    try:
        event = stripe_gateway.construct_event(body, stripe_signature)
    except stripe_gateway.GatewayNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured") from None
    except stripe_gateway.InvalidWebhook:
        raise HTTPException(status_code=400, detail="Invalid webhook") from None

    outcome = handle_stripe_event(db, event)
    return {"received": True, "outcome": outcome}


@router.get("", response_model=list[PaymentResponse])
def list_payments(
    order_id: int | None = Query(default=None, gt=0),
    payment_status: str | None = Query(default=None, max_length=30),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Payment)
    if current_user.role not in STAFF_ROLES:
        query = query.filter(Payment.user_id == current_user.id)
    if order_id:
        query = query.filter(Payment.order_id == order_id)
    if payment_status:
        query = query.filter(Payment.status == payment_status.upper())
    return query.order_by(Payment.id.desc()).offset(skip).limit(limit).all()


def _get_accessible_payment(db: Session, payment_id: int, user: User) -> Payment:
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if payment is None or (
        payment.user_id != user.id and user.role not in STAFF_ROLES
    ):
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_accessible_payment(db, payment_id, current_user)


@router.post("/{payment_id}/sync", response_model=PaymentResponse)
def sync_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ask Stripe directly for the session state (useful when webhooks cannot
    reach a local machine). Uses the same idempotent code path as the webhook."""
    payment = _get_accessible_payment(db, payment_id, current_user)
    if payment.status != "PENDING" or not payment.provider_reference:
        return payment

    try:
        session = stripe_gateway.retrieve_checkout_session(payment.provider_reference)
    except stripe_gateway.GatewayError:
        raise HTTPException(status_code=502, detail="The payment gateway is unavailable") from None

    if session.get("payment_status") == "paid":
        event_type, suffix = "checkout.session.completed", "paid"
    elif session.get("status") == "expired":
        event_type, suffix = "checkout.session.expired", "expired"
    else:
        return payment

    handle_stripe_event(
        db,
        {
            "id": f"sync:{session['id']}:{suffix}",
            "type": event_type,
            "data": {"object": session},
        },
    )
    db.refresh(payment)
    return payment
