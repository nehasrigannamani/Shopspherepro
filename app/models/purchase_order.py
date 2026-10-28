
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    __table_args__ = (
        CheckConstraint(
            "total_amount >= 0",
            name="ck_purchase_orders_total_nonnegative",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(40), unique=True, nullable=False, index=True)

    supplier_id = Column(
        Integer,
        ForeignKey("suppliers.id"),
        nullable=False,
        index=True,
    )

    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    currency = Column(String(3), nullable=False, default="INR")
    expected_delivery_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)

    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    approved_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    ordered_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)

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

    items = relationship(
        "PurchaseOrderItem",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderItem.id",
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "product_id",
            name="uq_purchase_order_product",
        ),
        CheckConstraint(
            "quantity_ordered > 0",
            name="ck_po_item_quantity_ordered_positive",
        ),
        CheckConstraint(
            "quantity_received >= 0",
            name="ck_po_item_quantity_received_nonnegative",
        ),
        CheckConstraint(
            "quantity_received <= quantity_ordered",
            name="ck_po_item_quantity_received_lte_ordered",
        ),
        CheckConstraint(
            "unit_cost >= 0",
            name="ck_po_item_unit_cost_nonnegative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="ck_po_item_line_total_nonnegative",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    purchase_order_id = Column(
        Integer,
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id = Column(
        Integer,
        ForeignKey("products.id"),
        nullable=False,
        index=True,
    )

    quantity_ordered = Column(Integer, nullable=False)
    quantity_received = Column(Integer, nullable=False, default=0)
    unit_cost = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)

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

    purchase_order = relationship(
        "PurchaseOrder",
        back_populates="items",
    )
