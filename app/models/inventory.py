
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("product_id", name="uq_inventory_product_id"),
        CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_inventory_quantity_nonnegative",
        ),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_inventory_reserved_nonnegative",
        ),
        CheckConstraint(
            "reserved_quantity <= quantity_on_hand",
            name="ck_inventory_reserved_lte_on_hand",
        ),
        CheckConstraint(
            "low_stock_threshold >= 0",
            name="ck_inventory_threshold_nonnegative",
        ),
        Index("ix_inventory_low_stock", "quantity_on_hand"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    quantity_on_hand: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    reserved_quantity: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    low_stock_threshold: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.reserved_quantity


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_inventory_movements_inventory_id", "inventory_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    inventory_id: Mapped[int] = mapped_column(
        ForeignKey("inventory.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_change: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)

    performed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
