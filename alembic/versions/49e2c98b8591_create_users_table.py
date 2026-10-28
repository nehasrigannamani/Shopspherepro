
"""Create users table.

Revision ID: 49e2c98b8591
Revises:
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "49e2c98b8591"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "SUPER_ADMIN",
                "ADMIN",
                "PRODUCT_MANAGER",
                "INVENTORY_MANAGER",
                "ORDER_MANAGER",
                "SUPPORT_AGENT",
                "CUSTOMER",
                "DELIVERY_MANAGER",
                "FINANCE_MANAGER",
                name="user_role",
            ),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone", name="uq_users_phone"),
    )

    op.create_index(
        "ix_users_id",
        "users",
        ["id"],
        unique=False,
    )

    op.create_index(
        "ix_users_email",
        "users",
        ["email"],
        unique=True,
    )


def downgrade() -> None:
    # Safe if the table is missing because the migration was
    # previously recorded as applied without creating the table.
    bind = op.get_bind()

    if sa.inspect(bind).has_table("users"):
        op.drop_index("ix_users_email", table_name="users")
        op.drop_index("ix_users_id", table_name="users")
        op.drop_table("users")
