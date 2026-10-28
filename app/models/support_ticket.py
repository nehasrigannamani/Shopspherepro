
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    __table_args__ = (
        Index(
            "ix_support_tickets_user_status",
            "user_id",
            "status",
        ),
        Index(
            "ix_support_tickets_assigned_status",
            "assigned_to",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    ticket_number: Mapped[str] = mapped_column(
        String(40),
        unique=True,
        index=True,
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"),
        nullable=True,
    )

    subject: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(50),
        default="GENERAL",
        nullable=False,
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        default="MEDIUM",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="OPEN",
        nullable=False,
    )

    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    resolution_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
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
