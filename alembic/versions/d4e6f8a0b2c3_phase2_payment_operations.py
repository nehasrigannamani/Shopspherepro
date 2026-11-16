"""phase 2: payment reconciliation, exception queue, settlements

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c3"
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "payment_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_reference", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("reported_gross", sa.Numeric(14, 2), nullable=False),
        sa.Column("reported_refunds", sa.Numeric(14, 2), nullable=False),
        sa.Column("reported_fees", sa.Numeric(14, 2), nullable=False),
        sa.Column("reported_net", sa.Numeric(14, 2), nullable=False),
        sa.Column("expected_gross", sa.Numeric(14, 2), nullable=False),
        sa.Column("expected_refunds", sa.Numeric(14, 2), nullable=False),
        sa.Column("variance", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("notes", sa.String(1000)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("provider", "provider_reference", name="uq_settlement_provider_ref"),
    )
    op.create_table(
        "payment_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_status", sa.String(30)),
        sa.Column("local_status_before", sa.String(30), nullable=False),
        sa.Column("local_status_after", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("detail", sa.String(500)),
        sa.Column("triggered_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
    )
    op.create_index("ix_payment_recon_payment", "payment_reconciliations", ["payment_id", "created_at"])
    op.create_table(
        "payment_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exc_type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(15), nullable=False, server_default="OPEN"),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL")),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("refund_id", sa.Integer(), sa.ForeignKey("refunds.id", ondelete="SET NULL")),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("payment_settlements.id", ondelete="SET NULL")),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("assigned_to", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime()),
        _ts(),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_payment_exceptions_dedupe"),
    )
    op.create_index("ix_payment_exceptions_status_type", "payment_exceptions", ["status", "exc_type"])


def downgrade() -> None:
    op.drop_table("payment_exceptions")
    op.drop_table("payment_reconciliations")
    op.drop_table("payment_settlements")
