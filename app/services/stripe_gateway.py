"""Thin wrapper around the Stripe SDK (test mode / sandbox).

Keeping every Stripe call here lets tests replace this module's functions
without any network access.
"""
from decimal import Decimal

import stripe

from app.core.config import settings


class GatewayError(Exception):
    """Raised when the payment gateway is unavailable or rejects a call."""


class GatewayNotConfigured(GatewayError):
    pass


class InvalidWebhook(ValueError):
    """Malformed payload or bad signature."""


def _api_key() -> str:
    if not settings.stripe_secret_key:
        raise GatewayNotConfigured("Stripe is not configured")
    return settings.stripe_secret_key


def to_minor_units(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


def create_checkout_session(
    *,
    order_number: str,
    order_id: int,
    user_id: int,
    customer_email: str,
    amount: Decimal,
    currency: str,
    idempotency_key: str,
) -> dict:
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "product_data": {
                            "name": f"ShopSphere order {order_number}"
                        },
                        "unit_amount": to_minor_units(amount),
                    },
                    "quantity": 1,
                }
            ],
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            customer_email=customer_email,
            metadata={"order_id": str(order_id), "user_id": str(user_id)},
            api_key=_api_key(),
            idempotency_key=idempotency_key,
        )
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GatewayError("Could not create a Stripe Checkout Session") from exc
    return {"id": session.id, "url": session.url}


def retrieve_checkout_session(session_id: str) -> dict:
    try:
        session = stripe.checkout.Session.retrieve(
            session_id, api_key=_api_key()
        )
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GatewayError("Could not retrieve the Stripe session") from exc
    return session.to_dict() if hasattr(session, "to_dict") else dict(session)


def construct_event(payload: bytes, signature: str | None) -> dict:
    """Verify the Stripe-Signature header and return the event dict.

    Raises ``ValueError`` for malformed payloads and
    ``stripe.SignatureVerificationError`` for bad signatures.
    """
    if not settings.stripe_webhook_secret:
        raise GatewayNotConfigured("Stripe webhook secret is not configured")
    if not signature:
        raise InvalidWebhook("Missing Stripe-Signature header")
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except Exception as exc:  # ValueError or SignatureVerificationError
        raise InvalidWebhook("Invalid webhook payload or signature") from exc
    return event.to_dict() if hasattr(event, "to_dict") else dict(event)


def create_refund(
    *,
    payment_intent_id: str,
    amount: Decimal,
    idempotency_key: str,
    refund_id: int,
) -> dict:
    try:
        refund = stripe.Refund.create(
            payment_intent=payment_intent_id,
            amount=to_minor_units(amount),
            metadata={"refund_id": str(refund_id)},
            api_key=_api_key(),
            idempotency_key=idempotency_key,
        )
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GatewayError("The payment gateway rejected the refund") from exc
    return {"id": refund.id, "status": refund.status}
