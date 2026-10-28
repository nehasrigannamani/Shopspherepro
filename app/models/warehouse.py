
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint(
            "capacity_units IS NULL OR capacity_units >= 0",
            name="ck_warehouses_capacity_nonnegative",
        ),
        Index("ix_warehouses_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Capacity is measured in total physical units, not volume or weight.
    capacity_units: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="ACTIVE", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WarehouseStock(Base):
    __tablename__ = "warehouse_stock"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id",
            "product_id",
            name="uq_warehouse_stock_warehouse_product",
        ),
        CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_warehouse_stock_quantity_nonnegative",
        ),
        Index("ix_warehouse_stock_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    quantity_on_hand: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WarehouseStockMovement(Base):
    __tablename__ = "warehouse_stock_movements"
    __table_args__ = (
        Index(
            "ix_warehouse_stock_movements_stock_id",
            "warehouse_stock_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    warehouse_stock_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_stock.id", ondelete="RESTRICT"),
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
