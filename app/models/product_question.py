
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProductQuestion(Base):
    __tablename__ = "product_questions"

    __table_args__ = (
        Index(
            "ix_product_questions_product_status",
            "product_id",
            "status",
        ),
        Index(
            "ix_product_questions_user_id",
            "user_id",
        ),
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

    question_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    answer_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    answered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="OPEN",
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
