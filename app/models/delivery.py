
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Delivery(Base):
    __tablename__ = "deliveries"

    __table_args__ = (
        UniqueConstraint(
            "shipping_order_id",
            name="uq_deliveries_shipping_order_id",
        ),
        Index("ix_deliveries_status", "status"),
        Index("ix_deliveries_assigned_to", "assigned_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    shipping_order_id: Mapped[int] = mapped_column(
        ForeignKey("shipping_orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    assigned_to: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30), default="ASSIGNED", nullable=False
    )

    recipient_name: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )

    delivery_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    failure_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
