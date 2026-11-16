from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PaymentReconciliation(Base):
    """One comparison of our payment record with the provider's."""

    __tablename__ = "payment_reconciliations"
    __table_args__ = (Index("ix_payment_recon_payment", "payment_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(30))  # PAID | EXPIRED | OPEN | None
    local_status_before: Mapped[str] = mapped_column(String(30), nullable=False)
    local_status_after: Mapped[str] = mapped_column(String(30), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500))
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PaymentException(Base):
    """A payment/refund/settlement anomaly that finance must review."""

    __tablename__ = "payment_exceptions"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_payment_exceptions_dedupe"),
        Index("ix_payment_exceptions_status_type", "status", "exc_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM")  # LOW|MEDIUM|HIGH
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="OPEN")  # OPEN|IN_REVIEW|RESOLVED|DISMISSED
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    refund_id: Mapped[int | None] = mapped_column(ForeignKey("refunds.id", ondelete="SET NULL"))
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("payment_settlements.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class PaymentSettlement(Base):
    """A provider payout statement compared against our own ledger."""

    __tablename__ = "payment_settlements"
    __table_args__ = (UniqueConstraint("provider", "provider_reference", name="uq_settlement_provider_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reported_gross: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reported_refunds: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reported_fees: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reported_net: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    expected_gross: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    expected_refunds: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    variance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False)  # MATCHED | VARIANCE
    notes: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
