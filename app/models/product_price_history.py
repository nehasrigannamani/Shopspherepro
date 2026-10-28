
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"

    id: Mapped[int] = mapped_column(primary_key=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    old_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    new_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    old_compare_at_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    new_compare_at_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
