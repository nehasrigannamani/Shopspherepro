from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CustomerSegment(Base):
    """A named customer group used to target promotions, coupons and loyalty rules."""

    __tablename__ = "customer_segments"
    __table_args__ = (UniqueConstraint("code", name="uq_customer_segments_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class CustomerSegmentMember(Base):
    __tablename__ = "customer_segment_members"
    __table_args__ = (UniqueConstraint("segment_id", "user_id", name="uq_segment_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("customer_segments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PromotionRedemption(Base):
    """One row per promotion/coupon actually applied to an order."""

    __tablename__ = "promotion_redemptions"
    __table_args__ = (
        CheckConstraint("(promotion_id IS NULL) <> (coupon_id IS NULL)", name="ck_redemption_one_target"),
        Index("ix_redemptions_user_promo", "user_id", "promotion_id", "status"),
        Index("ix_redemptions_user_coupon", "user_id", "coupon_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    promotion_id: Mapped[int | None] = mapped_column(ForeignKey("promotions.id", ondelete="SET NULL"))
    coupon_id: Mapped[int | None] = mapped_column(ForeignKey("coupons.id", ondelete="SET NULL"))
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="APPLIED", nullable=False)  # APPLIED | REVERSED
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class LoyaltyRule(Base):
    """Earning rule: ``earn_rate`` points per 1.00 of eligible (post-discount, pre-tax) spend."""

    __tablename__ = "loyalty_rules"
    __table_args__ = (
        CheckConstraint("earn_rate > 0", name="ck_loyalty_rule_rate_positive"),
        CheckConstraint("min_order_value >= 0", name="ck_loyalty_rule_min_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    earn_rate: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    min_order_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0", nullable=False)
    max_points_per_order: Mapped[int | None] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("customer_segments.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    expiry_days: Mapped[int | None] = mapped_column(Integer)  # None -> LOYALTY_DEFAULT_EXPIRY_DAYS; 0 -> never
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class LoyaltyLedger(Base):
    """Append-only points ledger. ``points`` is signed; the balance is the sum.
    Positive lots (EARN, REVERSE_REDEEM, ADJUST) track ``points_remaining`` for FIFO spending/expiry."""

    __tablename__ = "loyalty_ledger"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_loyalty_ledger_key"),
        Index("ix_loyalty_ledger_user", "user_id", "id"),
        Index("ix_loyalty_ledger_lots", "user_id", "points_remaining", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # EARN | REDEEM | EXPIRE | REVERSE_EARN | REVERSE_REDEEM | ADJUST
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    points_remaining: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
