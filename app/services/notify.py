"""Create in-app notifications and queue email/SMS deliveries.

Functions here only ``add``/``flush`` - the caller owns the transaction so a
notification is committed together with the business change that caused it.
"""
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.notification_delivery import NotificationDelivery
from app.models.user import User, UserRole


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    *,
    notification_type: str = "GENERAL",
    reference_type: str | None = None,
    reference_id: int | None = None,
    email: bool = False,
    sms: bool = False,
    email_subject: str | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        title=title[:200],
        message=message,
        notification_type=notification_type,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(notification)
    db.flush()

    if email or sms:
        user = db.get(User, user_id)
        if user is not None:
            if email and user.email:
                queue_delivery(
                    db,
                    user_id=user.id,
                    notification_id=notification.id,
                    channel="EMAIL",
                    recipient=user.email,
                    subject=email_subject or title,
                    message=message,
                )
            if sms and user.phone and os.getenv("SMS_PROVIDER_URL"):
                queue_delivery(
                    db,
                    user_id=user.id,
                    notification_id=notification.id,
                    channel="SMS",
                    recipient=user.phone,
                    subject=None,
                    message=message,
                )
    return notification


def queue_delivery(
    db: Session,
    *,
    user_id: int,
    channel: str,
    recipient: str,
    subject: str | None,
    message: str,
    notification_id: int | None = None,
) -> NotificationDelivery:
    delivery = NotificationDelivery(
        user_id=user_id,
        notification_id=notification_id,
        channel=channel,
        recipient=recipient,
        subject=subject,
        message=message,
        status="PENDING",
        attempts=0,
    )
    db.add(delivery)
    db.flush()
    return delivery


def notify_roles(
    db: Session,
    roles: tuple[UserRole, ...],
    title: str,
    message: str,
    *,
    notification_type: str = "GENERAL",
    reference_type: str | None = None,
    reference_id: int | None = None,
) -> int:
    """Notify every active user holding one of ``roles``."""
    user_ids = db.scalars(
        select(User.id).where(
            User.role.in_(roles), User.is_active.is_(True)
        )
    ).all()
    for user_id in user_ids:
        create_notification(
            db,
            user_id,
            title,
            message,
            notification_type=notification_type,
            reference_type=reference_type,
            reference_id=reference_id,
        )
    return len(user_ids)
