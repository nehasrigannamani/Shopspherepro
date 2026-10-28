from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_number: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(30), default="PLACED", nullable=False
    )
    payment_status: Mapped[str] = mapped_column(
        String(30), default="PENDING", nullable=False
    )
    payment_method: Mapped[str | None] = mapped_column(
    String(30), nullable=True
)
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", nullable=False
    )

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    shipping_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    grand_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    coupon_code: Mapped[str | None] = mapped_column(String(50))
    shipping_method_name: Mapped[str] = mapped_column(String(100), nullable=False)

    shipping_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("addresses.id", ondelete="SET NULL"), nullable=True
    )
    billing_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("addresses.id", ondelete="SET NULL"), nullable=True
    )

    shipping_full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    shipping_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    shipping_address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    shipping_address_line2: Mapped[str | None] = mapped_column(String(255))
    shipping_city: Mapped[str] = mapped_column(String(100), nullable=False)
    shipping_state: Mapped[str] = mapped_column(String(100), nullable=False)
    shipping_postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    shipping_country: Mapped[str] = mapped_column(String(2), nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "product_id",
            "variant_id",
            name="uq_order_product_variant",
        ),
        Index("ix_order_items_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )

    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id"), nullable=True
    )
    variant_sku: Mapped[str | None] = mapped_column(String(64))
    variant_options: Mapped[dict | None] = mapped_column(JSON)
    product_description: Mapped[str | None] = mapped_column(Text)
    fulfillment_status: Mapped[str] = mapped_column(
        String(30), default="PENDING", nullable=False
    )

    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)