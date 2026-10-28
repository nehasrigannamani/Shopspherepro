
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProductReview(Base):
    __tablename__ = "product_reviews"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_product_review_user_product",
        ),
        CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="ck_product_review_rating",
        ),
        Index("ix_product_reviews_product_status", "product_id", "status"),
        Index("ix_product_reviews_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id"),
        nullable=False,
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    review_text: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        default="PENDING",
        nullable=False,
    )

    moderation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    moderated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    moderated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
