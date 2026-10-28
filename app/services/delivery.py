"""Email/SMS sending with safe, bounded retries."""
import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import as_utc
from app.models.notification_delivery import NotificationDelivery

logger = logging.getLogger("shopsphere.delivery")

MAX_ATTEMPTS = 3
# Wait 2, 4, 8 ... minutes between automatic retries.
BACKOFF_BASE_MINUTES = 2


def send_email(delivery: NotificationDelivery) -> str | None:
    backend = os.getenv("EMAIL_BACKEND", "smtp").lower()

    if backend == "console":
        # Development helper: log the message instead of using SMTP.
        logger.info(
            "EMAIL to=%s subject=%s\n%s",
            delivery.recipient,
            delivery.subject,
            delivery.message,
        )
        return "console"

    host = os.getenv("EMAIL_HOST")
    username = os.getenv("EMAIL_USERNAME")
    password = os.getenv("EMAIL_PASSWORD")
    sender = os.getenv("EMAIL_FROM_ADDRESS")
    port = int(os.getenv("EMAIL_PORT", "587"))
    use_tls = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"

    if not host or not sender or not delivery.subject:
        raise RuntimeError("Email provider configuration is incomplete")

    email = EmailMessage()
    email["From"] = sender
    email["To"] = delivery.recipient
    email["Subject"] = delivery.subject
    email.set_content(delivery.message)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password or "")
        refused = smtp.send_message(email)
        if refused:
            raise RuntimeError("The email server refused the recipient")
    return None


def send_sms(delivery: NotificationDelivery) -> str | None:
    provider_url = os.getenv("SMS_PROVIDER_URL")
    provider_token = os.getenv("SMS_PROVIDER_TOKEN")

    if not provider_url or not provider_token:
        raise RuntimeError("SMS provider configuration is incomplete")

    response = httpx.post(
        provider_url,
        json={"to": delivery.recipient, "message": delivery.message},
        headers={
            "Authorization": f"Bearer {provider_token}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError:
        data = {}

    message_id = data.get("message_id") or data.get("id")
    return str(message_id) if message_id is not None else None


def attempt_delivery(
    db: Session, delivery: NotificationDelivery
) -> NotificationDelivery:
    """Make exactly ONE send attempt and record the outcome."""
    delivery.attempts += 1
    try:
        if delivery.channel == "EMAIL":
            provider_id = send_email(delivery)
        elif delivery.channel == "SMS":
            provider_id = send_sms(delivery)
        else:
            raise RuntimeError("Unsupported delivery channel")

        delivery.status = "SENT"
        delivery.provider_message_id = provider_id
        delivery.error_message = None
        delivery.sent_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        # Never store provider credentials or request headers.
        delivery.status = "FAILED"
        delivery.error_message = (
            f"{type(exc).__name__}: notification delivery failed"
        )
        logger.warning(
            "Delivery %s attempt %s failed: %s",
            delivery.id,
            delivery.attempts,
            type(exc).__name__,
        )

    db.commit()
    db.refresh(delivery)
    return delivery


def dispatch_pending(db: Session, limit: int = 50) -> dict:
    """Send PENDING deliveries and retry FAILED ones whose backoff elapsed."""
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(NotificationDelivery)
        .filter(
            or_(
                NotificationDelivery.status == "PENDING",
                (NotificationDelivery.status == "FAILED")
                & (NotificationDelivery.attempts < MAX_ATTEMPTS),
            )
        )
        .order_by(NotificationDelivery.id.asc())
        .limit(limit)
        .all()
    )

    sent = failed = skipped = 0
    for delivery in candidates:
        if delivery.status == "FAILED":
            wait = timedelta(
                minutes=BACKOFF_BASE_MINUTES ** max(delivery.attempts, 1)
            )
            last = delivery.updated_at or delivery.created_at
            if last is not None and now < as_utc(last) + wait:
                skipped += 1
                continue

        attempt_delivery(db, delivery)
        if delivery.status == "SENT":
            sent += 1
        else:
            failed += 1

    return {
        "examined": len(candidates),
        "sent": sent,
        "failed": failed,
        "skipped_backoff": skipped,
    }
