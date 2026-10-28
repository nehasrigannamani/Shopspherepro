
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.db.session import Base


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(Integer, primary_key=True, index=True)

    payment_id = Column(
        Integer,
        ForeignKey("payments.id"),
        nullable=False,
        index=True,
    )

    order_id = Column(
        Integer,
        ForeignKey("orders.id"),
        nullable=False,
        index=True,
    )

    requested_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    processed_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="INR")

    reason = Column(Text, nullable=False)
    status = Column(
        String(20),
        nullable=False,
        default="REQUESTED",
        index=True,
    )

    decision_note = Column(Text, nullable=True)
    provider_reference = Column(String(255), nullable=True, unique=True)
    failure_reason = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_refunds_order_status", "order_id", "status"),
    )
