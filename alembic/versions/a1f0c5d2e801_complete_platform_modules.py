"""complete platform: sessions, accounts, addresses, jobs, variants, refunds

Revision ID: a1f0c5d2e801
Revises: 4527d1c123c5
Create Date: 2026-10-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f0c5d2e801"
down_revision: Union[str, Sequence[str], None] = "4527d1c123c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts():
    return sa.Column(
        "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    # ---- users -------------------------------------------------------------
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    # ---- sessions / tokens -------------------------------------------------
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("revoked_reason", sa.String(100)),
        _ts(),
        sa.Column("last_used_at", sa.DateTime()),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(32), nullable=False, unique=True),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        _ts(),
    )
    op.create_index("ix_refresh_tokens_session", "refresh_tokens", ["session_id"])

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        _ts(),
    )
    op.create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])

    # ---- accounts ----------------------------------------------------------
    op.create_table(
        "addresses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address_type", sa.String(10), nullable=False, server_default="SHIPPING"),
        sa.Column("label", sa.String(50)),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("line1", sa.String(255), nullable=False),
        sa.Column("line2", sa.String(255)),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state", sa.String(100), nullable=False),
        sa.Column("postal_code", sa.String(20), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("is_default_shipping", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_default_billing", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts(),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_addresses_user_id", "addresses", ["user_id"])
    op.create_index("ix_addresses_user_type", "addresses", ["user_id", "address_type"])

    op.create_table(
        "user_activity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("detail", sa.String(255)),
        sa.Column("ip_address", sa.String(64)),
        _ts(),
    )
    op.create_index("ix_user_activity_user_created", "user_activity", ["user_id", "created_at"])

    op.create_table(
        "profile_change_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("old_value", sa.String(255)),
        sa.Column("new_value", sa.String(255)),
        _ts(),
    )
    op.create_index("ix_profile_history_user", "profile_change_history", ["user_id", "created_at"])

    op.create_table(
        "deactivation_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decision_note", sa.Text()),
        _ts(),
        sa.Column("decided_at", sa.DateTime()),
    )
    op.create_index("ix_deactivation_requests_user_id", "deactivation_requests", ["user_id"])
    op.create_index("ix_deactivation_requests_status", "deactivation_requests", ["status"])

    # ---- background jobs ---------------------------------------------------
    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_name", sa.String(50), nullable=False),
        sa.Column("export_format", sa.String(10), nullable=False, server_default="csv"),
        sa.Column("filters", sa.JSON()),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
    )
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("params", sa.JSON()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("result_path", sa.String(500)),
        sa.Column("result_summary", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("report_schedules.id", ondelete="SET NULL")),
        _ts(),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_background_jobs_status_created", "background_jobs", ["status", "created_at"])

    # ---- payments / refunds ------------------------------------------------
    op.add_column("payments", sa.Column("provider_payment_intent", sa.String(255)))
    op.create_index("ix_payments_provider_payment_intent", "payments", ["provider_payment_intent"])

    op.add_column("refunds", sa.Column("provider_reference", sa.String(255)))
    op.create_unique_constraint("uq_refunds_provider_reference", "refunds", ["provider_reference"])
    op.add_column("refunds", sa.Column("failure_reason", sa.Text()))
    op.add_column("refunds", sa.Column("processed_at", sa.DateTime(timezone=True)))

    op.alter_column(
        "order_status_history", "changed_by",
        existing_type=sa.Integer(), nullable=True,
    )

    # ---- cart items: variants ---------------------------------------------
    op.add_column("cart_items", sa.Column("variant_id", sa.Integer()))
    op.create_foreign_key(
        "fk_cart_items_variant", "cart_items", "product_variants",
        ["variant_id"], ["id"], ondelete="RESTRICT",
    )
    # Create the new unique key BEFORE dropping the old one so the cart_id
    # foreign key always has a usable index (MySQL error 1553 otherwise).
    op.create_unique_constraint(
        "uq_cart_items_cart_product_variant", "cart_items",
        ["cart_id", "product_id", "variant_id"],
    )
    op.drop_constraint("uq_cart_items_cart_product", "cart_items", type_="unique")

    # ---- orders / order items ---------------------------------------------
    op.add_column("orders", sa.Column("shipping_address_id", sa.Integer()))
    op.add_column("orders", sa.Column("billing_address_id", sa.Integer()))
    op.create_foreign_key(
        "fk_orders_shipping_address", "orders", "addresses",
        ["shipping_address_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_billing_address", "orders", "addresses",
        ["billing_address_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column("order_items", sa.Column("variant_id", sa.Integer()))
    op.add_column("order_items", sa.Column("variant_sku", sa.String(64)))
    op.add_column("order_items", sa.Column("variant_options", sa.JSON()))
    op.add_column("order_items", sa.Column("product_description", sa.Text()))
    op.add_column(
        "order_items",
        sa.Column("fulfillment_status", sa.String(30), nullable=False, server_default="PENDING"),
    )
    op.create_foreign_key(
        "fk_order_items_variant", "order_items", "product_variants",
        ["variant_id"], ["id"],
    )
    op.create_unique_constraint(
        "uq_order_product_variant", "order_items",
        ["order_id", "product_id", "variant_id"],
    )
    op.drop_constraint("uq_order_product", "order_items", type_="unique")

    # ---- audit logs: make them append-only (MySQL) -------------------------
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            "CREATE TRIGGER trg_audit_logs_no_update BEFORE UPDATE ON audit_logs "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT = 'audit_logs is append-only'"
        )
        op.execute(
            "CREATE TRIGGER trg_audit_logs_no_delete BEFORE DELETE ON audit_logs "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT = 'audit_logs is append-only'"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update")

    op.create_unique_constraint("uq_order_product", "order_items", ["order_id", "product_id"])
    op.drop_constraint("uq_order_product_variant", "order_items", type_="unique")
    op.drop_constraint("fk_order_items_variant", "order_items", type_="foreignkey")
    for column in ("fulfillment_status", "product_description", "variant_options", "variant_sku", "variant_id"):
        op.drop_column("order_items", column)

    op.drop_constraint("fk_orders_billing_address", "orders", type_="foreignkey")
    op.drop_constraint("fk_orders_shipping_address", "orders", type_="foreignkey")
    op.drop_column("orders", "billing_address_id")
    op.drop_column("orders", "shipping_address_id")

    op.create_unique_constraint("uq_cart_items_cart_product", "cart_items", ["cart_id", "product_id"])
    op.drop_constraint("uq_cart_items_cart_product_variant", "cart_items", type_="unique")
    op.drop_constraint("fk_cart_items_variant", "cart_items", type_="foreignkey")
    op.drop_column("cart_items", "variant_id")

    op.alter_column("order_status_history", "changed_by", existing_type=sa.Integer(), nullable=False)

    op.drop_column("refunds", "processed_at")
    op.drop_column("refunds", "failure_reason")
    op.drop_constraint("uq_refunds_provider_reference", "refunds", type_="unique")
    op.drop_column("refunds", "provider_reference")

    op.drop_index("ix_payments_provider_payment_intent", table_name="payments")
    op.drop_column("payments", "provider_payment_intent")

    op.drop_table("background_jobs")
    op.drop_table("report_schedules")
    op.drop_table("deactivation_requests")
    op.drop_table("profile_change_history")
    op.drop_table("user_activity")
    op.drop_table("addresses")
    op.drop_table("email_verification_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("auth_sessions")
    op.drop_column("users", "last_login_at")
