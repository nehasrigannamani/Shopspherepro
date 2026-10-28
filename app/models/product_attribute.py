
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProductAttribute(Base):
    __tablename__ = "product_attributes"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "category_id",
            name="uq_attribute_name_category",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    data_type: Mapped[str] = mapped_column(
        String(30), default="TEXT", nullable=False
    )

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    is_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_filterable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ProductAttributeValue(Base):
    __tablename__ = "product_attribute_values"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "attribute_id",
            name="uq_product_attribute_value",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("product_attributes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    value: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
