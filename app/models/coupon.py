from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Coupon(Base):
    __tablename__ = "coupons"

    __table_args__ = (
        CheckConstraint(
            "discount_value > 0",
            name="ck_coupon_discount_positive",
        ),
        CheckConstraint(
            "usage_limit IS NULL OR usage_limit > 0",
            name="ck_coupon_usage_limit_positive",
        ),
        CheckConstraint(
            "used_count >= 0",
            name="ck_coupon_used_count_nonnegative",
        ),
        CheckConstraint(
            "min_order_amount >= 0",
            name="ck_coupon_min_order_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(String(255))

    discount_type: Mapped[str] = mapped_column(
        Enum("PERCENTAGE", "FIXED", name="coupon_discount_type"),
        nullable=False,
    )

    discount_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    min_order_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    usage_limit: Mapped[int | None] = mapped_column(Integer)

    used_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )