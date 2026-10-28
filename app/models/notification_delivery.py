
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    __table_args__ = (
        Index(
            "ix_notification_deliveries_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_notification_deliveries_user_channel",
            "user_id",
            "channel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )

    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    recipient: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="PENDING",
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    provider_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
