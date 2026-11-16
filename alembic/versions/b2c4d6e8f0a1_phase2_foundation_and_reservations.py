"""phase 2: SELLER role, idempotency keys, inventory reservations

Revision ID: b2c4d6e8f0a1
Revises: a1f0c5d2e801
Create Date: 2026-11-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, Sequence[str], None] = "a1f0c5d2e801"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_ROLES = (
    "SUPER_ADMIN", "ADMIN", "PRODUCT_MANAGER", "INVENTORY_MANAGER", "ORDER_MANAGER",
    "SUPPORT_AGENT", "CUSTOMER", "DELIVERY_MANAGER", "FINANCE_MANAGER",
)
NEW_ROLES = OLD_ROLES + ("SELLER",)


def upgrade() -> None:
    # MySQL ENUM: the full value list must be restated.
    op.alter_column(
        "users", "role",
        existing_type=sa.Enum(*OLD_ROLES, name="user_role"),
        type_=sa.Enum(*NEW_ROLES, name="user_role"),
        existing_nullable=False,
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
    )

    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.String(32), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="RESTRICT")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("release_reason", sa.String(255)),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
    )
    op.create_index("ix_reservations_status_expires", "inventory_reservations", ["status", "expires_at"])
    op.create_index("ix_reservations_user_status", "inventory_reservations", ["user_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    sellers = bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE role = 'SELLER'")).scalar()
    if sellers:
        raise RuntimeError(
            f"Cannot downgrade: {sellers} SELLER user(s) exist. Reassign or remove them first."
        )
    op.drop_table("inventory_reservations")
    op.drop_table("idempotency_keys")
    op.alter_column(
        "users", "role",
        existing_type=sa.Enum(*NEW_ROLES, name="user_role"),
        type_=sa.Enum(*OLD_ROLES, name="user_role"),
        existing_nullable=False,
    )
