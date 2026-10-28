from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Promotion(Base):
    __tablename__ = "promotions"

    __table_args__ = (
        CheckConstraint(
            "discount_value > 0",
            name="ck_promotion_discount_positive",
        ),
        CheckConstraint(
            "usage_limit IS NULL OR usage_limit > 0",
            name="ck_promotion_usage_limit_positive",
        ),
        CheckConstraint(
            "used_count >= 0",
            name="ck_promotion_used_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    discount_type: Mapped[str] = mapped_column(
        Enum("PERCENTAGE", "FIXED", name="promotion_discount_type"),
        nullable=False,
    )
    discount_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # ALL, PRODUCT, or CATEGORY
    target_type: Mapped[str] = mapped_column(
        Enum("ALL", "PRODUCT", "CATEGORY", name="promotion_target_type"),
        default="ALL",
        nullable=False,
    )

    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    usage_limit: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    priority: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
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