
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WarehouseStockThreshold(Base):
    __tablename__ = "warehouse_stock_thresholds"
    __table_args__ = (
        CheckConstraint(
            "threshold >= 0",
            name="ck_warehouse_stock_threshold_nonnegative",
        ),
        Index(
            "ix_warehouse_stock_threshold_value",
            "threshold",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    warehouse_stock_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_stock.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    threshold: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LowStockThresholdHistory(Base):
    __tablename__ = "low_stock_threshold_history"
    __table_args__ = (
        CheckConstraint(
            "old_threshold IS NULL OR old_threshold >= 0",
            name="ck_low_stock_history_old_nonnegative",
        ),
        CheckConstraint(
            "new_threshold >= 0",
            name="ck_low_stock_history_new_nonnegative",
        ),
        Index("ix_low_stock_history_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Exactly one target should be set: a global inventory record
    # or a warehouse-specific stock record.
    inventory_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory.id", ondelete="RESTRICT"),
        nullable=True,
    )
    warehouse_stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouse_stock.id", ondelete="RESTRICT"),
        nullable=True,
    )

    old_threshold: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    new_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
