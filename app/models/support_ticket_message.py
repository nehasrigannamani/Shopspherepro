
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    __table_args__ = (
        Index(
            "ix_support_ticket_messages_ticket_created",
            "ticket_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )

    sender_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
