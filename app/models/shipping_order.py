
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


class ShippingOrder(Base):
    __tablename__ = "shipping_orders"

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_shipping_orders_order_id"),
        Index("ix_shipping_orders_status", "status"),
        Index("ix_shipping_orders_tracking_number", "tracking_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    carrier_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )

    tracking_number: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), default="PREPARING", nullable=False
    )

    estimated_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
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
