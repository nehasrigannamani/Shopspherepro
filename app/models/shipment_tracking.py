
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


class ShipmentTracking(Base):
    __tablename__ = "shipment_tracking"

    __table_args__ = (
        Index(
            "ix_shipment_tracking_order_event",
            "shipping_order_id",
            "event_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    shipping_order_id: Mapped[int] = mapped_column(
        ForeignKey("shipping_orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
