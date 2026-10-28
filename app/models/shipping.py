from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ShippingMethod(Base):
    __tablename__ = "shipping_methods"

    __table_args__ = (
        CheckConstraint("base_fee >= 0", name="ck_shipping_base_fee_nonnegative"),
        CheckConstraint(
            "free_shipping_threshold IS NULL OR free_shipping_threshold >= 0",
            name="ck_shipping_threshold_nonnegative",
        ),
        CheckConstraint(
            "estimated_min_days >= 0",
            name="ck_shipping_min_days_nonnegative",
        ),
        CheckConstraint(
            "estimated_max_days >= estimated_min_days",
            name="ck_shipping_max_days_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    base_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    free_shipping_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    estimated_min_days: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False
    )
    estimated_max_days: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )