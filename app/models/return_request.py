
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


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    __table_args__ = (
        Index("ix_return_requests_order_id", "order_id"),
        Index("ix_return_requests_status", "status"),
        Index("ix_return_requests_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), default="REQUESTED", nullable=False
    )

    decision_note: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    processed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    requested_at: Mapped[datetime] = mapped_column(
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
