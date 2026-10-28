
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Payment(Base):
    __tablename__ = "payments"

    __table_args__ = (
        Index(
            "ix_payments_order_created",
            "order_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(30),
        default="STRIPE",
        nullable=False,
    )

    provider_reference: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )

    provider_payment_intent: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    checkout_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        default="INR",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="PENDING",
        nullable=False,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
