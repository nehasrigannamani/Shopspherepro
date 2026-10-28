
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notifications import (
    NotificationBulkRead,
    NotificationCreate,
    NotificationResponse,
)


router = APIRouter(
    prefix="/notifications",
    tags=["Notification Management"],
)


STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "PRODUCT_MANAGER",
    "INVENTORY_MANAGER",
    "ORDER_MANAGER",
    "SUPPORT_AGENT",
    "DELIVERY_MANAGER",
    "FINANCE_MANAGER",
)


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notification(
    payload: NotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    recipient = (
        db.query(User)
        .filter(User.id == payload.user_id)
        .first()
    )

    if recipient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification recipient not found",
        )

    notification = Notification(
        user_id=payload.user_id,
        title=payload.title,
        message=payload.message,
        notification_type=payload.notification_type,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)

    return notification


@router.get(
    "/mine",
    response_model=list[NotificationResponse],
)
def get_my_notifications(
    unread_only: bool = False,
    notification_type: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Notification).filter(
        Notification.user_id == current_user.id
    )

    if unread_only:
        query = query.filter(Notification.is_read.is_(False))

    if notification_type:
        query = query.filter(
            Notification.notification_type == notification_type.upper()
        )

    return (
        query.order_by(Notification.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/mine/unread-count")
def get_unread_notification_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .count()
    )

    return {"unread_count": count}


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
)
def mark_notification_as_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)

    return notification


@router.post("/mine/read-all")
def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    updated_count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .update(
            {
                Notification.is_read: True,
                Notification.read_at: now,
            },
            synchronize_session=False,
        )
    )

    db.commit()

    return {
        "message": "Unread notifications marked as read",
        "updated_count": updated_count,
    }


@router.post("/mine/read-selected")
def mark_selected_notifications_as_read(
    payload: NotificationBulkRead,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification_ids = list(set(payload.notification_ids))
    now = datetime.now(timezone.utc)

    updated_count = (
        db.query(Notification)
        .filter(
            Notification.id.in_(notification_ids),
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .update(
            {
                Notification.is_read: True,
                Notification.read_at: now,
            },
            synchronize_session=False,
        )
    )

    db.commit()

    return {
        "message": "Selected notifications marked as read",
        "updated_count": updated_count,
    }


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    db.delete(notification)
    db.commit()

    return None


@router.delete("/mine/clear-read")
def delete_read_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted_count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(True),
        )
        .delete(synchronize_session=False)
    )

    db.commit()

    return {
        "message": "Read notifications deleted",
        "deleted_count": deleted_count,
    }
