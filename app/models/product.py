
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_name", "name"),
        Index("ix_products_category", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    sku: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    brand_id: Mapped[int | None] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    compare_at_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    image_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
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
