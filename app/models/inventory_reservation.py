from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class InventoryReservation(Base):
    """A time-boxed hold on stock. While ACTIVE it is counted in the stock
    row's ``reserved_quantity`` so no one else can buy those units."""

    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        Index("ix_reservations_status_expires", "status", "expires_at"),
        Index("ix_reservations_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # ACTIVE | CONVERTED (became an order) | RELEASED (manual/cancel/payment failure) | EXPIRED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    release_reason: Mapped[str | None] = mapped_column(String(255))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
