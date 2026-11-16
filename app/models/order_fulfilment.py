from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OrderShipment(Base):
    """One parcel of a split shipment. Created already SHIPPED; becomes DELIVERED."""

    __tablename__ = "order_shipments"
    __table_args__ = (
        UniqueConstraint("shipment_number", name="uq_order_shipments_number"),
        Index("ix_order_shipments_order", "order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    shipment_number: Mapped[str] = mapped_column(String(60), nullable=False)
    carrier_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="SHIPPED", nullable=False)  # SHIPPED | DELIVERED
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class OrderShipmentItem(Base):
    __tablename__ = "order_shipment_items"
    __table_args__ = (
        UniqueConstraint("shipment_id", "order_item_id", name="uq_shipment_item"),
        CheckConstraint("quantity > 0", name="ck_shipment_item_qty_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("order_shipments.id", ondelete="CASCADE"), nullable=False)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)


class OrderBackorder(Base):
    __tablename__ = "order_backorders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_backorder_qty_positive"),
        Index("ix_order_backorders_order_status", "order_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)  # OPEN | RESOLVED | CANCELLED
    expected_at: Mapped[datetime | None] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class OrderItemCancellation(Base):
    __tablename__ = "order_item_cancellations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_item_cancel_qty_positive"),
        Index("ix_item_cancellations_order", "order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    refund_id: Mapped[int | None] = mapped_column(ForeignKey("refunds.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
