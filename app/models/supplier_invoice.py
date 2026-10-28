
from sqlalchemy import (
    Column, Integer, String, Date, DateTime, ForeignKey,
    Numeric, Text, CheckConstraint, Index,
)
from sqlalchemy.sql import func

from app.db.session import Base


class SupplierInvoice(Base):
    __tablename__ = "supplier_invoices"

    __table_args__ = (
        CheckConstraint(
            "subtotal >= 0",
            name="ck_supplier_invoice_subtotal_nonnegative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_supplier_invoice_tax_nonnegative",
        ),
        CheckConstraint(
            "total_amount >= 0",
            name="ck_supplier_invoice_total_nonnegative",
        ),
        Index(
            "ix_supplier_invoice_supplier_status",
            "supplier_id",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(
        String(100), nullable=False, unique=True, index=True
    )
    supplier_invoice_number = Column(String(100), nullable=True)

    supplier_id = Column(
        Integer, ForeignKey("suppliers.id"), nullable=False, index=True
    )
    purchase_order_id = Column(
        Integer,
        ForeignKey("purchase_orders.id"),
        nullable=True,
        index=True,
    )

    invoice_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    currency = Column(String(3), nullable=False, default="INR")

    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    tax_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)

    status = Column(
        String(20), nullable=False, default="RECORDED", index=True
    )
    notes = Column(Text, nullable=True)

    created_by = Column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)

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
