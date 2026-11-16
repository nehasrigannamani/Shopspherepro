"""phase 2: order fulfilment (split shipments, back-orders, item cancellation)

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-11-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b2c4d6e8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    for col in ("quantity_shipped", "quantity_delivered", "quantity_cancelled", "quantity_backordered"):
        op.add_column("order_items", sa.Column(col, sa.Integer(), nullable=False, server_default="0"))

    # Orders cancelled or shipped before this migration: bring counters in line.
    op.execute("UPDATE order_items SET quantity_cancelled = quantity WHERE fulfillment_status = 'CANCELLED'")
    op.execute(
        "UPDATE order_items SET quantity_shipped = quantity "
        "WHERE fulfillment_status IN ('SHIPPED', 'DELIVERED')"
    )
    op.execute("UPDATE order_items SET quantity_delivered = quantity WHERE fulfillment_status = 'DELIVERED'")
    op.execute(
        "UPDATE order_items oi JOIN orders o ON o.id = oi.order_id "
        "SET oi.quantity_shipped = oi.quantity "
        "WHERE o.status IN ('SHIPPED', 'DELIVERED') AND oi.quantity_cancelled = 0"
    )
    op.execute(
        "UPDATE order_items oi JOIN orders o ON o.id = oi.order_id "
        "SET oi.quantity_delivered = oi.quantity "
        "WHERE o.status = 'DELIVERED' AND oi.quantity_cancelled = 0"
    )

    op.create_table(
        "order_shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shipment_number", sa.String(60), nullable=False),
        sa.Column("carrier_name", sa.String(100), nullable=False),
        sa.Column("tracking_number", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False, server_default="SHIPPED"),
        sa.Column("estimated_delivery_at", sa.DateTime()),
        sa.Column("shipped_at", sa.DateTime()),
        sa.Column("delivered_at", sa.DateTime()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts(),
        sa.UniqueConstraint("shipment_number", name="uq_order_shipments_number"),
    )
    op.create_index("ix_order_shipments_order", "order_shipments", ["order_id"])

    op.create_table(
        "order_shipment_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("order_shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.UniqueConstraint("shipment_id", "order_item_id", name="uq_shipment_item"),
        sa.CheckConstraint("quantity > 0", name="ck_shipment_item_qty_positive"),
    )

    op.create_table(
        "order_backorders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("expected_at", sa.DateTime()),
        sa.Column("reason", sa.String(500)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime()),
        _ts(),
        sa.CheckConstraint("quantity > 0", name="ck_backorder_qty_positive"),
    )
    op.create_index("ix_order_backorders_order_status", "order_backorders", ["order_id", "status"])

    op.create_table(
        "order_item_cancellations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("refund_id", sa.Integer(), sa.ForeignKey("refunds.id", ondelete="SET NULL")),
        _ts(),
        sa.CheckConstraint("quantity > 0", name="ck_item_cancel_qty_positive"),
    )
    op.create_index("ix_item_cancellations_order", "order_item_cancellations", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_item_cancellations")
    op.drop_table("order_backorders")
    op.drop_table("order_shipment_items")
    op.drop_table("order_shipments")
    for col in ("quantity_backordered", "quantity_cancelled", "quantity_delivered", "quantity_shipped"):
        op.drop_column("order_items", col)
