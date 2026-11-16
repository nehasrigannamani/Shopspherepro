"""phase 2: pricing rules, customer segments, redemptions, loyalty points

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-11-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f7a9b1c3d4"
down_revision: Union[str, Sequence[str], None] = "d4e6f8a0b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "customer_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("code", name="uq_customer_segments_code"),
    )
    op.create_table(
        "customer_segment_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("customer_segments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("segment_id", "user_id", name="uq_segment_member"),
    )
    op.create_index("ix_customer_segment_members_user_id", "customer_segment_members", ["user_id"])

    # promotions
    op.add_column("promotions", sa.Column("min_cart_value", sa.Numeric(12, 2)))
    op.add_column("promotions", sa.Column("max_discount", sa.Numeric(12, 2)))
    op.add_column("promotions", sa.Column("stackable", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("promotions", sa.Column("allow_with_coupon", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("promotions", sa.Column("per_customer_limit", sa.Integer()))
    op.add_column("promotions", sa.Column("customer_segment_id", sa.Integer()))
    op.create_foreign_key("fk_promotions_segment", "promotions", "customer_segments",
                          ["customer_segment_id"], ["id"], ondelete="SET NULL")
    # coupons
    op.add_column("coupons", sa.Column("max_discount", sa.Numeric(12, 2)))
    op.add_column("coupons", sa.Column("per_customer_limit", sa.Integer()))
    op.add_column("coupons", sa.Column("customer_segment_id", sa.Integer()))
    op.create_foreign_key("fk_coupons_segment", "coupons", "customer_segments",
                          ["customer_segment_id"], ["id"], ondelete="SET NULL")
    # orders
    op.add_column("orders", sa.Column("loyalty_points_redeemed", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("loyalty_discount", sa.Numeric(12, 2), nullable=False, server_default="0.00"))

    op.create_table(
        "promotion_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("promotion_id", sa.Integer(), sa.ForeignKey("promotions.id", ondelete="SET NULL")),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="SET NULL")),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="APPLIED"),
        sa.Column("reversed_at", sa.DateTime()),
        _ts(),
        sa.CheckConstraint("(promotion_id IS NULL) <> (coupon_id IS NULL)", name="ck_redemption_one_target"),
    )
    op.create_index("ix_promotion_redemptions_order_id", "promotion_redemptions", ["order_id"])
    op.create_index("ix_redemptions_user_promo", "promotion_redemptions", ["user_id", "promotion_id", "status"])
    op.create_index("ix_redemptions_user_coupon", "promotion_redemptions", ["user_id", "coupon_id", "status"])

    op.create_table(
        "loyalty_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("earn_rate", sa.Numeric(10, 4), nullable=False),
        sa.Column("min_order_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("max_points_per_order", sa.Integer()),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE")),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("customer_segments.id", ondelete="CASCADE")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expiry_days", sa.Integer()),
        sa.Column("starts_at", sa.DateTime()),
        sa.Column("ends_at", sa.DateTime()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.CheckConstraint("earn_rate > 0", name="ck_loyalty_rule_rate_positive"),
        sa.CheckConstraint("min_order_value >= 0", name="ck_loyalty_rule_min_nonnegative"),
    )
    op.create_table(
        "loyalty_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("points_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("idempotency_key", sa.String(100)),
        sa.Column("reason", sa.String(500)),
        sa.Column("details", sa.JSON()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("idempotency_key", name="uq_loyalty_ledger_key"),
    )
    op.create_index("ix_loyalty_ledger_user", "loyalty_ledger", ["user_id", "id"])
    op.create_index("ix_loyalty_ledger_lots", "loyalty_ledger", ["user_id", "points_remaining", "expires_at"])


def downgrade() -> None:
    op.drop_table("loyalty_ledger")
    op.drop_table("loyalty_rules")
    op.drop_table("promotion_redemptions")
    op.drop_column("orders", "loyalty_discount")
    op.drop_column("orders", "loyalty_points_redeemed")
    op.drop_constraint("fk_coupons_segment", "coupons", type_="foreignkey")
    for col in ("customer_segment_id", "per_customer_limit", "max_discount"):
        op.drop_column("coupons", col)
    op.drop_constraint("fk_promotions_segment", "promotions", type_="foreignkey")
    for col in ("customer_segment_id", "per_customer_limit", "allow_with_coupon", "stackable", "max_discount", "min_cart_value"):
        op.drop_column("promotions", col)
    op.drop_table("customer_segment_members")
    op.drop_table("customer_segments")
