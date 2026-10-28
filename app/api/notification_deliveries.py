
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.notification import Notification
from app.models.notification_delivery import NotificationDelivery
from app.models.user import User
from app.services.delivery import (
    MAX_ATTEMPTS,
    attempt_delivery,
    dispatch_pending,
)
from app.schemas.notification_deliveries import (
    EmailNotificationCreate,
    NotificationDeliveryResponse,
    SMSNotificationCreate,
)


router = APIRouter(
    prefix="/notification-deliveries",
    tags=["Email and SMS Notifications"],
)

STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "SUPPORT_AGENT",
)



def _create_delivery(
    db: Session,
    user_id: int,
    notification_id: int | None,
    channel: str,
    recipient: str,
    subject: str | None,
    message: str,
) -> NotificationDelivery:
    if db.query(User.id).filter(User.id == user_id).first() is None:
        raise HTTPException(
            status_code=404,
            detail="Notification recipient user not found",
        )

    if notification_id is not None:
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .first()
        )

        if notification is None:
            raise HTTPException(
                status_code=400,
                detail="Notification does not belong to the specified user",
            )

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
    db.commit()
    db.refresh(delivery)

    return delivery


def _deliver(
    db: Session,
    delivery: NotificationDelivery,
) -> NotificationDelivery:
    """One send attempt; failures can be retried later via /retry."""
    return attempt_delivery(db, delivery)


@router.post(
    "/email",
    response_model=NotificationDeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def send_email_notification(
    payload: EmailNotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    delivery = _create_delivery(
        db=db,
        user_id=payload.user_id,
        notification_id=payload.notification_id,
        channel="EMAIL",
        recipient=str(payload.recipient),
        subject=payload.subject,
        message=payload.message,
    )

    return _deliver(db, delivery)


@router.post(
    "/sms",
    response_model=NotificationDeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def send_sms_notification(
    payload: SMSNotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    delivery = _create_delivery(
        db=db,
        user_id=payload.user_id,
        notification_id=payload.notification_id,
        channel="SMS",
        recipient=payload.recipient,
        subject=None,
        message=payload.message,
    )

    return _deliver(db, delivery)


@router.get(
    "/mine",
    response_model=list[NotificationDeliveryResponse],
)
def get_my_delivery_history(
    channel: str | None = Query(
        default=None,
        pattern=r"^(EMAIL|SMS)$",
    ),
    delivery_status: str | None = Query(
        default=None,
        pattern=r"^(PENDING|SENT|FAILED)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(NotificationDelivery).filter(
        NotificationDelivery.user_id == current_user.id
    )

    if channel:
        query = query.filter(
            NotificationDelivery.channel == channel
        )

    if delivery_status:
        query = query.filter(
            NotificationDelivery.status == delivery_status
        )

    return (
        query.order_by(NotificationDelivery.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "",
    response_model=list[NotificationDeliveryResponse],
)
def list_delivery_attempts(
    delivery_status: str | None = Query(
        default=None,
        pattern=r"^(PENDING|SENT|FAILED)$",
    ),
    channel: str | None = Query(
        default=None,
        pattern=r"^(EMAIL|SMS)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    query = db.query(NotificationDelivery)

    if delivery_status:
        query = query.filter(
            NotificationDelivery.status == delivery_status
        )

    if channel:
        query = query.filter(
            NotificationDelivery.channel == channel
        )

    return (
        query.order_by(NotificationDelivery.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("/dispatch")
def dispatch_deliveries(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    """Send queued deliveries and retry eligible failures (with backoff)."""
    return dispatch_pending(db, limit=limit)


@router.post(
    "/{delivery_id}/retry",
    response_model=NotificationDeliveryResponse,
)
def retry_failed_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    delivery = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.id == delivery_id)
        .first()
    )

    if delivery is None:
        raise HTTPException(
            status_code=404,
            detail="Notification delivery record not found",
        )

    if delivery.status != "FAILED":
        raise HTTPException(
            status_code=400,
            detail="Only failed deliveries can be retried",
        )

    if delivery.attempts >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=400,
            detail="Maximum delivery attempts reached",
        )

    return _deliver(db, delivery)
