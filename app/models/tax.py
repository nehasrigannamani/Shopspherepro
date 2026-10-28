from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TaxRate(Base):
    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)

    # Optional category-specific rate. A null category applies generally.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    region_code: Mapped[str | None] = mapped_column(String(50))
    is_inclusive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )