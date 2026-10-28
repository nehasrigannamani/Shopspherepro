
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "sku",
            name="uq_product_variant_product_sku",
        ),
        CheckConstraint("price > 0", name="ck_variant_price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    sku: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )

    # Example: {"size": "M", "color": "Blue"}
    option_values: Mapped[dict] = mapped_column(
        JSON, nullable=False
    )

    price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
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
